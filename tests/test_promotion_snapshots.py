"""回归验证：版本事实、排队输入、重试和修订并发保护。"""
import threading
from concurrent.futures import ThreadPoolExecutor
import pytest

from easel.promotion import db, generation, jobs, service
from easel.promotion.schemas import (
    AssetInput, CampaignCreate, CampaignUpdate, ContentCreate, ContentEdit,
    FactInput, FactUpdate, ProductCreate, ProductUpdate, ReviewDecision, VersionCreate,
)


@pytest.fixture()
def snapshot_db(tmp_path, monkeypatch):
    monkeypatch.setenv("EASEL_PROMOTION_DB", str(tmp_path / "promotion.db"))
    monkeypatch.setattr(service, "OUTPUTS_DIR", tmp_path / "outputs")
    service.OUTPUTS_DIR.mkdir()
    monkeypatch.setattr(generation, "_llm_json", lambda *args, **kwargs: None)
    # 同步测试排队与恢复边界，不启动后台线程。
    monkeypatch.setattr(jobs, "start_job", lambda job_id: None)
    db.reset_thread_connection()
    db.init_db()
    yield
    db.reset_thread_connection()


def _ready_campaign():
    product = service.create_product(ProductCreate(
        name="原产品", tagline="原介绍", default_persona="开发者本人",
        forbidden_claims="保证成功", website="https://example.test"))
    fact = service.add_fact(product["id"], FactInput(
        category="feature", content="原版批量处理", launch_status="launched"))
    service.update_fact(product["id"], fact["id"], FactUpdate(confirmed=True))
    pending = service.add_fact(product["id"], FactInput(
        category="feature", content="尚未确认的秘密功能", launch_status="launched"))
    version = service.create_version(product["id"], VersionCreate(version_label="v1"))
    service.confirm_version(product["id"], version["id"])
    campaign = service.create_campaign(CampaignCreate(
        product_id=product["id"], version_id=version["id"], template="launch",
        channels=["xiaohongshu"], voice="原语气"))
    service.update_campaign(campaign["id"], CampaignUpdate(
        brief_confirmed=True, angles=[{"angle": "原角度", "hook": "原切入"}]))
    content = service.create_content(ContentCreate(
        campaign_id=campaign["id"], platform="xiaohongshu", format="note", angle="原角度"))
    return product, fact, pending, version, campaign, content


def test_context_uses_pinned_version_and_keeps_pending_facts_out(snapshot_db):
    product, fact, pending, version, campaign, content = _ready_campaign()
    service.update_fact(product["id"], fact["id"], FactUpdate(content="后来改写的功能"))
    context = generation.build_context(campaign["id"])
    assert context["version"]["id"] == version["id"]
    assert [f["content"] for f in context["facts"]] == ["原版批量处理"]
    assert [f["content"] for f in context["pending_facts"]] == [pending["content"]]
    prompts = []
    generation._llm_json = lambda prompt: prompts.append(prompt)
    generation.generate_content(content["id"])
    assert "原版批量处理" in prompts[0]
    assert "尚未确认的秘密功能" not in prompts[0]
    assert "后来改写的功能" not in prompts[0]
    assert "开发者本人" in prompts[0] and "保证成功" in prompts[0]


def test_enqueue_freezes_inputs_and_persists_revision_provenance(snapshot_db):
    product, fact, _, version, campaign, content = _ready_campaign()
    shot = service.OUTPUTS_DIR / "shot.png"
    shot.write_bytes(b"screenshot")
    asset = service.add_asset(product["id"], AssetInput(
        kind="screenshot", name="原截图", path=shot.name, version_id=version["id"]))
    service.edit_content(content["id"], ContentEdit(asset_refs=[asset["id"]]))
    job = jobs.create_job("content_draft", content_id=content["id"])
    snapshot = job["params"]["input_snapshot"]
    assert job["campaign_id"] == campaign["id"]
    assert any(j["id"] == job["id"] for j in service.get_campaign(campaign["id"])["jobs"])
    service.update_product(product["id"], ProductUpdate(name="后来产品", default_persona="后来身份"))
    service.update_fact(product["id"], fact["id"], FactUpdate(content="后来功能"))
    service.update_campaign(campaign["id"], CampaignUpdate(
        voice="后来语气", angles=[{"angle": "原角度", "hook": "后来切入"}]))
    db.reset_thread_connection()  # 排队输入在新连接读取后仍应完整。
    jobs._run_job_safe(job["id"])
    assert jobs.get_job(job["id"])["status"] == "succeeded"
    revision = service.get_content(content["id"])["revisions"][-1]
    assert "原产品" in revision["title"] and "原版批量处理" in revision["body"]
    assert "后来" not in revision["body"]
    assert revision["input_snapshot"] == snapshot
    assert revision["asset_refs"] == [asset["id"]]
    assert service.get_content(content["id"])["status"] == "ready"


def test_retry_uses_original_inputs_instead_of_recapturing(snapshot_db, monkeypatch):
    product, fact, _, _, campaign, content = _ready_campaign()
    job = jobs.create_job("content_draft", content_id=content["id"])
    jobs._update(job["id"], status="failed", error="模拟外部服务失败")
    service.update_product(product["id"], ProductUpdate(name="新名称"))
    service.update_fact(product["id"], fact["id"], FactUpdate(content="新事实"))
    service.update_campaign(campaign["id"], CampaignUpdate(brief_confirmed=False))
    retried = jobs.retry_job(job["id"])
    assert retried["params"] == job["params"]
    jobs._run_job_safe(retried["id"])
    assert jobs.get_job(retried["id"])["status"] == "succeeded"
    revision = service.get_content(content["id"])["revisions"][-1]
    body = revision["body"]
    assert "原产品" in revision["title"] and "原版批量处理" in body
    assert "新名称" not in body and "新事实" not in body


def test_late_generation_does_not_overwrite_user_edit(snapshot_db, monkeypatch):
    _, _, _, _, _, content = _ready_campaign()
    job = jobs.create_job("content_draft", content_id=content["id"])

    def edit_while_generating(prompt):
        service.edit_content(content["id"], ContentEdit(title="用户的新标题", body="用户的新正文"))
        return None

    monkeypatch.setattr(generation, "_llm_json", edit_while_generating)
    jobs._run_job_safe(job["id"])
    assert jobs.get_job(job["id"])["status"] == "failed"
    latest = service.get_content(content["id"])["revisions"][-1]
    assert latest["title"] == "用户的新标题"
    assert latest["body"] == "用户的新正文"


def test_check_job_cannot_record_old_result_on_new_revision(snapshot_db, monkeypatch):
    _, _, _, _, _, content = _ready_campaign()
    job = jobs.create_job("checks", content_id=content["id"])

    def check_with_edit(item, revision):
        service.edit_content(item["id"], ContentEdit(title="检查期间修改"))
        return []

    monkeypatch.setattr(jobs, "run_checks", check_with_edit)
    jobs._run_job_safe(job["id"])
    assert jobs.get_job(job["id"])["status"] == "failed"
    assert service.get_content(content["id"])["revisions"][-1]["review"] is None


def test_legacy_task_without_snapshot_must_be_recreated(snapshot_db):
    _, _, _, _, _, content = _ready_campaign()
    job = jobs.create_job("content_draft", content_id=content["id"])
    jobs._update(job["id"], params="{}")
    jobs._run_job_safe(job["id"])
    failed = jobs.get_job(job["id"])
    assert failed["status"] == "failed" and "快照" in failed["error"]
    with pytest.raises(service.PromotionError, match="快照"):
        jobs.retry_job(job["id"])


def test_unconfirmed_profile_or_plan_cannot_enqueue_generation(snapshot_db):
    product = service.create_product(ProductCreate(name="未确认产品"))
    campaign = service.create_campaign(CampaignCreate(product_id=product["id"], template="launch"))
    with pytest.raises(service.PromotionError):
        jobs.create_job("brief", campaign_id=campaign["id"])
    version = service.create_version(product["id"], VersionCreate(version_label="v1"))
    service.confirm_version(product["id"], version["id"])
    content = service.create_content(ContentCreate(
        campaign_id=campaign["id"], platform="xiaohongshu", format="note"))
    with pytest.raises(service.PromotionError, match="方案"):
        jobs.create_job("content_draft", content_id=content["id"])
    assert jobs.list_jobs() == []


@pytest.mark.parametrize("job_type", ["brief", "angles"])
def test_campaign_jobs_use_enqueued_identity_and_preferences(snapshot_db, monkeypatch, job_type):
    product, _, _, _, campaign, _ = _ready_campaign()
    job = jobs.create_job(job_type, campaign_id=campaign["id"])
    service.update_product(product["id"], ProductUpdate(
        name="新名称", default_persona="新身份", forbidden_claims="新红线"))
    service.update_campaign(campaign["id"], CampaignUpdate(voice="新语气"))
    prompts = []
    monkeypatch.setattr(generation, "_llm_json", lambda prompt: prompts.append(prompt))
    jobs._run_job_safe(job["id"])
    assert jobs.get_job(job["id"])["status"] == "succeeded"
    assert len(prompts) == 1
    assert all(text in prompts[0] for text in ("原产品", "开发者本人", "保证成功", "原语气"))
    assert all(text not in prompts[0] for text in ("新名称", "新身份", "新红线", "新语气"))


def test_unbound_campaign_pins_once_instead_of_following_new_versions(snapshot_db):
    product, _, _, version, _, _ = _ready_campaign()
    campaign = service.create_campaign(CampaignCreate(product_id=product["id"], template="launch"))
    first = generation.build_context(campaign["id"])
    newer = service.create_version(product["id"], VersionCreate(version_label="v2"))
    service.confirm_version(product["id"], newer["id"])
    second = generation.build_context(campaign["id"])
    assert first["version"]["id"] == version["id"]
    assert second["version"]["id"] == version["id"]
    assert service.get_campaign(campaign["id"])["version_id"] == version["id"]



def test_review_uses_pinned_pricing_instead_of_new_version(snapshot_db):
    product = service.create_product(ProductCreate(name="定价产品"))
    pricing = service.add_fact(product["id"], FactInput(
        category="pricing", content="每月99元", launch_status="launched"))
    service.update_fact(product["id"], pricing["id"], FactUpdate(confirmed=True))
    version = service.create_version(product["id"], VersionCreate(version_label="v1"))
    service.confirm_version(product["id"], version["id"])
    campaign = service.create_campaign(CampaignCreate(
        product_id=product["id"], version_id=version["id"], template="launch"))
    service.update_fact(product["id"], pricing["id"], FactUpdate(content="完全免费"))
    newer = service.create_version(product["id"], VersionCreate(version_label="v2"))
    service.confirm_version(product["id"], newer["id"])
    content = service.create_content(ContentCreate(
        campaign_id=campaign["id"], platform="xiaohongshu", format="note",
        title="定价产品免费", body="定价产品完全免费，欢迎体验"))
    with pytest.raises(service.PromotionError, match="检查项"):
        service.approve_content(content["id"], ReviewDecision(approved=True))
    latest = service.get_content(content["id"])["revisions"][-1]
    assert latest["review"]["approved"] is False
    assert any(issue["item"] == "定价与限制" and issue["severity"] == "error"
               for issue in latest["review"]["checks"])
    # v2 活动可以依据自己的已确认定价事实审核相同表述。
    newer_campaign = service.create_campaign(CampaignCreate(
        product_id=product["id"], version_id=newer["id"], template="launch"))
    newer_content = service.create_content(ContentCreate(
        campaign_id=newer_campaign["id"], platform="xiaohongshu", format="note",
        title="定价产品免费", body="定价产品完全免费，欢迎体验"))
    assert service.approve_content(newer_content["id"], ReviewDecision(approved=True))["status"] == "reviewed"


def test_concurrent_file_registration_keeps_one_owner_and_exportable_asset(snapshot_db):
    products = [service.create_product(ProductCreate(name=name)) for name in ("产品A", "产品B")]
    campaigns = {}
    for product in products:
        version = service.create_version(product["id"], VersionCreate(version_label="v1"))
        service.confirm_version(product["id"], version["id"])
        campaigns[product["id"]] = service.create_campaign(CampaignCreate(
            product_id=product["id"], version_id=version["id"], template="launch"))
    # 非 _promotion 目录是已有内容库文件的兼容入口，仍只允许一个产品拥有文件。
    shared = service.OUTPUTS_DIR / "shared-shot.png"
    shared.write_bytes(b"original screenshot")
    barrier = threading.Barrier(2)

    def register(product):
        try:
            barrier.wait(timeout=5)
            return service.add_asset(product["id"], AssetInput(
                kind="screenshot", name="截图", path=shared.name))
        except service.PromotionError as exc:
            return exc
        finally:
            db.reset_thread_connection()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(register, products))
    winners = [result for result in results if isinstance(result, dict)]
    rejected = [result for result in results if isinstance(result, service.PromotionError)]
    assert len(winners) == len(rejected) == 1
    assert "其他产品" in str(rejected[0])
    assert db.connect().execute("SELECT COUNT(*) FROM assets").fetchone()[0] == 1
    asset = winners[0]
    owner = next(product for product in products if product["id"] == asset["product_id"])
    campaign = campaigns[owner["id"]]
    content = service.create_content(ContentCreate(
        campaign_id=campaign["id"], platform="xiaohongshu", format="note",
        title=owner["name"], body=f"{owner['name']}欢迎体验"))
    service.edit_content(content["id"], ContentEdit(asset_refs=[asset["id"]]))
    service.approve_content(content["id"], ReviewDecision(approved=True))
    package = service.export_campaign_zip(campaign["id"])
    import zipfile
    with zipfile.ZipFile(package) as archive:
        media = [name for name in archive.namelist() if name.endswith("-" + shared.name)]
        assert len(media) == 1
        assert archive.read(media[0]) == b"original screenshot"
