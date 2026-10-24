"""Regression coverage for promotion review, ownership and export boundaries."""
from __future__ import annotations

import json
import sqlite3
import zipfile
from io import BytesIO
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from easel.promotion import db, generation, router, service
from easel.promotion.schemas import (
    AssetInput, CampaignCreate, ContentCreate, ContentEdit, FactInput, FactUpdate,
    MetricImport, MetricImportRow, MetricInput, ProductCreate, ProductUpdate, PublicationInput,
    ReviewDecision, TrackingLinkInput, VersionCreate,
)


@pytest.fixture(autouse=True)
def isolated_promotion(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("EASEL_PROMOTION_DB", str(tmp_path / "promotion.db"))
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    monkeypatch.setattr(service, "OUTPUTS_DIR", outputs)
    monkeypatch.setattr(router, "OUTPUTS_DIR", outputs)
    monkeypatch.setattr(generation, "_llm_json", lambda *_args, **_kw: None)
    db.reset_thread_connection()
    db.init_db()
    yield tmp_path / "promotion.db"
    db.reset_thread_connection()


def product(name="A"):
    return service.create_product(ProductCreate(name=name, website="https://example.test"))


def version(pid, label="v1"):
    item = service.create_version(pid, VersionCreate(version_label=label))
    return service.confirm_version(pid, item["id"])


def campaign(pid, **kw):
    return service.create_campaign(CampaignCreate(product_id=pid, template="launch", **kw))


def content(campaign_id, **kw):
    return service.create_content(ContentCreate(
        campaign_id=campaign_id, platform="xiaohongshu", format="note",
        title=kw.pop("title", "A 产品"),
        body=kw.pop("body", "A 产品介绍，访问 https://example.test"), **kw))


def approve(item):
    return service.approve_content(item["id"], ReviewDecision(
        approved=True, revision_id=item["revisions"][-1]["id"]))


def asset(pid, name="shot.png", **kw):
    destination = service.OUTPUTS_DIR / pid / name
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(b"screenshot fixture")
    return service.add_asset(pid, AssetInput(
        kind="screenshot", name=name,
        path=destination.relative_to(service.OUTPUTS_DIR).as_posix(), **kw))


def persisted_status(database, content_id):
    with sqlite3.connect(database) as conn:
        return conn.execute("SELECT status FROM content_variants WHERE id=?",
                            (content_id,)).fetchone()[0]


def test_check_and_campaign_response_decode_checks_as_arrays():
    p = product()
    c = campaign(p["id"])
    item = content(c["id"])
    app = FastAPI()
    app.include_router(router.router)
    with TestClient(app) as client:
        response = client.post(f"/api/promotion/contents/{item['id']}/check")
        assert response.status_code == 200
        review = response.json()["content"]["revisions"][-1]["review"]
        assert isinstance(review["checks"], list)
        detail = client.get(f"/api/promotion/campaigns/{c['id']}").json()
        assert isinstance(detail["campaign"]["contents"][0]["latest_revision"]["review"]["checks"], list)


@pytest.mark.parametrize("body", ["", "产品完全免费，快来体验"])
@pytest.mark.parametrize("fabricate_clean_check", [False, True])
def test_approval_rechecks_content_instead_of_trusting_absent_or_empty_results(body, fabricate_clean_check):
    p = product()
    item = content(campaign(p["id"])["id"], body=body)
    if fabricate_clean_check:
        service.record_check(item["id"], [])
    with pytest.raises(service.PromotionError):
        approve(item)
    assert service.get_content(item["id"])["status"] != "reviewed"


def test_approval_rejects_revision_the_user_no_longer_has_open():
    p = product()
    item = content(campaign(p["id"])["id"])
    stale_id = item["revisions"][-1]["id"]
    service.edit_content(item["id"], ContentEdit(title="更新标题"))
    with pytest.raises(service.PromotionError):
        service.approve_content(item["id"], ReviewDecision(approved=True, revision_id=stale_id))
    assert service.get_content(item["id"])["revisions"][-1]["review"] is None


def test_background_check_cannot_attach_old_results_to_new_revision():
    p = product()
    item = content(campaign(p["id"])["id"])
    old_id = item["revisions"][-1]["id"]
    service.edit_content(item["id"], ContentEdit(body="新正文 https://example.test"))
    with pytest.raises(service.PromotionError):
        service.record_check(item["id"], [], revision_id=old_id)
    assert service.get_content(item["id"])["revisions"][-1]["review"] is None


@pytest.mark.parametrize("change", ["fact_content", "fact_confirmation", "new_fact", "product", "version"])
def test_fact_and_profile_changes_commit_review_invalidation_across_connections(isolated_promotion, change):
    p = product()
    fact = service.add_fact(p["id"], FactInput(category="feature", content="批量处理", launch_status="launched"))
    fact = service.update_fact(p["id"], fact["id"], FactUpdate(confirmed=True))
    item = content(campaign(p["id"])["id"])
    approve(item)
    assert persisted_status(isolated_promotion, item["id"]) == "reviewed"
    if change == "fact_content":
        service.update_fact(p["id"], fact["id"], FactUpdate(content="能力撤回", launch_status="planned"))
    elif change == "fact_confirmation":
        service.update_fact(p["id"], fact["id"], FactUpdate(confirmed=False))
    elif change == "new_fact":
        service.add_fact(p["id"], FactInput(category="limit", content="仅限测试环境"))
    elif change == "product":
        service.update_product(p["id"], ProductUpdate(forbidden_claims="旧口号"))
    else:
        version(p["id"], "v2")
    assert persisted_status(isolated_promotion, item["id"]) == "ready"
    db.reset_thread_connection()
    latest = service.get_content(item["id"])["revisions"][-1]
    assert latest["review"]["invalidated_at"] is not None
    with pytest.raises(service.PromotionError):
        service.export_campaign_zip(item["campaign_id"])


def test_confirmation_does_not_confirm_pending_facts_or_rewrite_snapshot():
    p = product()
    fact = service.add_fact(p["id"], FactInput(category="pricing", content="待核实价格"))
    draft = service.create_version(p["id"], VersionCreate(version_label="v1"))
    confirmed = service.confirm_version(p["id"], draft["id"])
    assert confirmed["fact_snapshot"] == draft["fact_snapshot"]
    assert not confirmed["fact_snapshot"][0]["confirmed"]
    assert service.get_product(p["id"])["facts"][0]["id"] == fact["id"]
    assert service.get_product(p["id"])["facts"][0]["confirmed"] is False


def test_cross_product_versions_are_rejected_for_campaigns_and_assets():
    a, b = product("A"), product("B")
    a_version = version(a["id"])
    with pytest.raises(service.PromotionError):
        campaign(b["id"], version_id=a_version["id"])
    with pytest.raises(service.PromotionError):
        asset(b["id"], version_id=a_version["id"])
    assert service.list_campaigns(b["id"]) == []
    assert service.list_assets(b["id"]) == []


@pytest.mark.parametrize("ref_kind", ["foreign", "missing"])
def test_edit_rejects_foreign_or_missing_assets_before_persisting_revision(ref_kind):
    a, b = product("A"), product("B")
    item = content(campaign(b["id"])["id"])
    ref = asset(a["id"])["id"] if ref_kind == "foreign" else "asset-missing"
    with pytest.raises(service.PromotionError):
        service.edit_content(item["id"], ContentEdit(asset_refs=[ref]))
    assert len(service.get_content(item["id"])["revisions"]) == 1


def test_clearing_asset_refs_and_structure_keeps_saved_generation_provenance():
    p = product()
    item = content(campaign(p["id"])["id"])
    shot = asset(p["id"])
    original_snapshot = {"schema_version": 1, "context": {"product": {"id": p["id"]}}}
    service.edit_content(item["id"], ContentEdit(
        asset_refs=[shot["id"]], structure={"pages": [{"text": "内容"}]}),
        input_snapshot=original_snapshot)
    changed = service.edit_content(item["id"], ContentEdit(asset_refs=[], structure={}))
    latest = changed["revisions"][-1]
    assert latest["asset_refs"] == []
    assert latest["structure"] == {}
    assert latest["input_snapshot"] == original_snapshot


@pytest.mark.parametrize("operation", ["metric", "metric_import", "tracking_link"])
def test_metric_and_tracking_content_must_belong_to_target_campaign(operation):
    a, b = product("A"), product("B")
    a_campaign, b_campaign = campaign(a["id"]), campaign(b["id"])
    foreign_content = content(a_campaign["id"])
    with pytest.raises(service.PromotionError):
        if operation == "metric":
            service.add_metric(b_campaign["id"], MetricInput(metric="views", value=1, content_id=foreign_content["id"]))
        elif operation == "metric_import":
            service.import_metrics(b_campaign["id"], MetricImport(batch="b1", rows=[
                MetricImportRow(metric="views", value=1),
                MetricImportRow(metric="views", value=1, content_id=foreign_content["id"]),
            ]))
        else:
            service.create_tracking_link(b_campaign["id"], TrackingLinkInput(
                target_url="https://example.test", content_id=foreign_content["id"]))
    assert service.metrics_summary(b_campaign["id"])["by_metric"] == {}
    assert service.list_tracking_links(b_campaign["id"]) == []


def test_export_refuses_legacy_foreign_asset_even_with_saved_approval():
    a, b = product("A"), product("B")
    shot = asset(a["id"])
    item = content(campaign(b["id"])["id"])
    approve(item)
    conn = db.connect()
    conn.execute("UPDATE content_revisions SET asset_refs=? WHERE id=?",
                 (json.dumps([shot["id"]]), item["revisions"][-1]["id"]))
    conn.commit()
    with pytest.raises(service.PromotionError):
        service.export_campaign_zip(item["campaign_id"])
    assert not list((service.OUTPUTS_DIR / "_promotion" / "exports").glob("*.zip"))


def test_post_download_contains_reviewed_revision_and_only_marks_exported():
    p = product()
    item = content(campaign(p["id"])["id"])
    approve(item)
    app = FastAPI()
    app.include_router(router.router)
    with TestClient(app) as client:
        url = f"/api/promotion/campaigns/{item['campaign_id']}/export-zip"
        assert client.get(url).status_code == 405
        response = client.post(url)
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/zip"
        assert "attachment" in response.headers["content-disposition"]
        with zipfile.ZipFile(BytesIO(response.content)) as archive:
            text_file = next(name for name in archive.namelist() if name.startswith("xiaohongshu/") and name.endswith(".md"))
            assert "A 产品介绍" in archive.read(text_file).decode()
    assert service.get_content(item["id"])["status"] == "exported"
    assert service.list_publications(item["campaign_id"]) == []


def test_reconfirming_same_version_does_not_invalidate_new_approval():
    p = product()
    confirmed = version(p["id"])
    item = content(campaign(p["id"], version_id=confirmed["id"])["id"])
    approve(item)
    service.confirm_version(p["id"], confirmed["id"])
    assert service.get_content(item["id"])["status"] == "reviewed"


@pytest.mark.parametrize("status", ["exported", "published"])
def test_fact_change_invalidates_exported_and_published_content_without_erasing_publications(status):
    p = product()
    fact = service.add_fact(p["id"], FactInput(category="feature", content="批量处理", launch_status="launched"))
    item = content(campaign(p["id"])["id"])
    approve(item)
    if status == "exported":
        service.export_campaign_zip(item["campaign_id"])
    else:
        service.backfill_publication(item["id"], PublicationInput(
            channel="xiaohongshu", link="https://example.test/published"))
    assert service.get_content(item["id"])["status"] == status
    service.update_fact(p["id"], fact["id"], FactUpdate(content="更新的事实"))
    assert service.get_content(item["id"])["status"] == "ready"
    with pytest.raises(service.PromotionError):
        service.export_campaign_zip(item["campaign_id"])
    publications = service.list_publications(item["campaign_id"])
    assert len(publications) == (1 if status == "published" else 0)
    if publications:
        assert publications[0]["link"] == "https://example.test/published"


@pytest.mark.parametrize("registered", [False, True])
def test_asset_registration_cannot_alias_another_products_path(registered):
    a, b = product("A"), product("B")
    foreign_file = service.OUTPUTS_DIR / "_promotion" / "assets" / a["id"] / "private.png"
    foreign_file.parent.mkdir(parents=True)
    foreign_file.write_bytes(b"product-A-only")
    relative = foreign_file.relative_to(service.OUTPUTS_DIR).as_posix()
    if registered:
        service.add_asset(a["id"], AssetInput(kind="screenshot", name="A", path=relative))
    with pytest.raises(service.PromotionError):
        service.add_asset(b["id"], AssetInput(kind="screenshot", name="B", path=relative))
    assert service.list_assets(b["id"]) == []


def test_export_rechecks_path_after_asset_is_replaced_by_external_symlink(tmp_path):
    p = product()
    shot = asset(p["id"])
    item = content(campaign(p["id"])["id"])
    item = service.edit_content(item["id"], ContentEdit(asset_refs=[shot["id"]]))
    approve(item)
    private_file = tmp_path / "private.txt"
    private_file.write_text("must never enter ZIP")
    source = service.OUTPUTS_DIR / shot["path"]
    source.unlink()
    source.symlink_to(private_file)
    with pytest.raises(service.PromotionError):
        service.export_campaign_zip(item["campaign_id"])
    assert not list((service.OUTPUTS_DIR / "_promotion" / "exports").glob("*.zip"))


def test_asset_version_must_match_campaign_even_for_same_product():
    p = product()
    old = version(p["id"], "v1")
    newer = version(p["id"], "v2")
    shot = asset(p["id"], version_id=newer["id"])
    item = content(campaign(p["id"], version_id=old["id"])["id"])
    with pytest.raises(service.PromotionError):
        service.edit_content(item["id"], ContentEdit(asset_refs=[shot["id"]]))
    assert service.get_content(item["id"])["revisions"][-1]["asset_refs"] == []


def test_api_requires_explicit_revision_for_manual_review():
    p = product()
    item = content(campaign(p["id"])["id"])
    app = FastAPI()
    app.include_router(router.router)
    with TestClient(app) as client:
        response = client.post(f"/api/promotion/contents/{item['id']}/review", json={"approved": True})
        assert response.status_code == 400
        assert "revision_id" in response.json()["detail"]
    assert service.get_content(item["id"])["status"] != "reviewed"


def test_new_blocking_check_revokes_prior_approval_and_blocks_export(monkeypatch):
    from easel.promotion import checks

    p = product()
    item = content(campaign(p["id"])["id"])
    approve(item)
    # Channel rules can change independently of a saved content revision.
    monkeypatch.setitem(checks.PLATFORM_RULES, "xiaohongshu", {"title_max": 1, "body_max": 1000, "label": "小红书"})
    app = FastAPI()
    app.include_router(router.router)
    with TestClient(app) as client:
        response = client.post(f"/api/promotion/contents/{item['id']}/check")
        assert response.status_code == 200
        assert any(issue["severity"] == "error" for issue in response.json()["issues"])
        latest = response.json()["content"]["revisions"][-1]
        assert latest["review"]["approved"] is False
        assert response.json()["content"]["status"] == "ready"
        approval = client.post(f"/api/promotion/contents/{item['id']}/review", json={
            "approved": True, "revision_id": latest["id"],
        })
        assert approval.status_code == 400
        assert client.post(f"/api/promotion/campaigns/{item['campaign_id']}/export-zip").status_code == 400


def test_api_campaign_flow_preserves_snapshot_review_and_download_boundaries(monkeypatch):
    from easel.promotion import jobs

    monkeypatch.setattr(jobs, "start_job", jobs._run_job_safe)
    app = FastAPI()
    app.include_router(router.router)
    with TestClient(app) as client:
        response = client.post("/api/promotion/products", json={
            "name": "A", "tagline": "产品助手", "website": "https://example.test",
        })
        assert response.status_code == 200
        pid = response.json()["product"]["id"]
        response = client.post(f"/api/promotion/products/{pid}/facts", json={
            "category": "feature", "content": "支持任务整理", "launch_status": "launched",
        })
        assert response.status_code == 200
        fid = response.json()["fact"]["id"]
        assert client.put(f"/api/promotion/products/{pid}/facts/{fid}", json={"confirmed": True}).status_code == 200
        response = client.post(f"/api/promotion/products/{pid}/versions", json={"version_label": "v1"})
        assert response.status_code == 200
        vid = response.json()["version"]["id"]
        assert client.post(f"/api/promotion/products/{pid}/versions/{vid}/confirm").status_code == 200
        response = client.post("/api/promotion/campaigns", json={
            "product_id": pid, "version_id": vid, "template": "launch", "channels": ["xiaohongshu"],
        })
        assert response.status_code == 200
        cid = response.json()["campaign"]["id"]
        response = client.post(f"/api/promotion/campaigns/{cid}/jobs/angles")
        assert response.status_code == 200
        assert response.json()["job"]["status"] == "succeeded"
        response = client.post(f"/api/promotion/campaigns/{cid}/confirm-plan")
        assert response.status_code == 200
        created = response.json()["created"]
        assert created
        assert client.post(f"/api/promotion/campaigns/{cid}/confirm-plan").json()["created"] == []
        item = next(item for item in created if item["format"] == "note")
        response = client.post(f"/api/promotion/contents/{item['id']}/jobs/content_draft")
        assert response.status_code == 200
        job = response.json()["job"]
        assert job["status"] == "succeeded", job["error"]
        assert job["campaign_id"] == cid
        response = client.post(f"/api/promotion/contents/{item['id']}/check")
        assert response.status_code == 200
        revision = response.json()["content"]["revisions"][-1]
        assert revision["input_snapshot"] == job["params"]["input_snapshot"]
        assert revision["input_snapshot"]["context"]["version"]["id"] == vid
        assert isinstance(revision["review"]["checks"], list)
        response = client.post(f"/api/promotion/contents/{item['id']}/review", json={
            "approved": True, "revision_id": revision["id"],
        })
        assert response.status_code == 200
        assert response.json()["content"]["status"] == "reviewed"
        downloaded = client.post(f"/api/promotion/campaigns/{cid}/export-zip")
        assert downloaded.status_code == 200
        with zipfile.ZipFile(BytesIO(downloaded.content)) as archive:
            document = next(name for name in archive.namelist() if name.startswith("xiaohongshu/") and name.endswith(".md"))
            assert "支持任务整理" in archive.read(document).decode()
        assert client.get(f"/api/promotion/contents/{item['id']}").json()["content"]["status"] == "exported"
        assert client.get(f"/api/promotion/campaigns/{cid}/publications").json()["publications"] == []
