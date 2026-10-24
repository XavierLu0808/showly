"""生成任务编排与持久化（功能文档 4.11 / 实施方案 3.5）。

- 状态机：queued → running → succeeded / failed / cancelled。
- 任务输入快照、步骤、产物、错误摘要全部落库；重启后 running 任务标记为
  failed(interrupted) 可重试，已 succeeded 不重复执行。
- 幂等键：同一 content + job_type 的进行中任务只允许一个；生成任务重试
  不会创建发布记录（发布只能人工回填），结构上杜绝重复提交。
"""
from __future__ import annotations

import copy
import json
import threading
import traceback

from . import db, generation, service
from .checks import run_checks
from .schemas import CampaignUpdate, ContentEdit
from .service import PromotionError, now

_workers: dict[str, threading.Thread] = {}
_lock = threading.Lock()


def _row(job_id: str) -> dict:
    conn = db.connect()
    row = conn.execute("SELECT * FROM generation_jobs WHERE id=?", (job_id,)).fetchone()
    if row is None:
        raise PromotionError(f"任务不存在：{job_id}")
    return _to_dict(row)


def _to_dict(row) -> dict:
    data = dict(row)
    for field in ("params", "steps", "artifacts"):
        try:
            data[field] = json.loads(data[field] or ("{}" if field == "params" else "[]"))
        except json.JSONDecodeError:
            data[field] = {} if field == "params" else []
    return data


def list_jobs(campaign_id: str | None = None, content_id: str | None = None) -> list[dict]:
    conn = db.connect()
    query = "SELECT * FROM generation_jobs"
    conditions, args = [], []
    if campaign_id:
        conditions.append("campaign_id=?")
        args.append(campaign_id)
    if content_id:
        conditions.append("content_id=?")
        args.append(content_id)
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY created_at DESC LIMIT 100"
    return [_to_dict(r) for r in conn.execute(query, args).fetchall()]


def get_job(job_id: str) -> dict:
    return _row(job_id)


def recover_interrupted_jobs() -> int:
    """启动恢复：把上次服务退出时仍 running/queued 的任务标记为失败（可重试）。"""
    conn = db.connect()
    cursor = conn.execute(
        "UPDATE generation_jobs SET status='failed', error='服务重启中断，可重试',"
        " updated_at=?, finished_at=? WHERE status IN ('queued','running')",
        (now(), now()),
    )
    conn.commit()
    return cursor.rowcount


def create_job(job_type: str, *, campaign_id: str | None = None,
               content_id: str | None = None, params: dict | None = None,
               auto_start: bool = True, _input_snapshot: dict | None = None) -> dict:
    if job_type not in ("brief", "angles", "content_draft", "checks"):
        raise PromotionError(f"未知任务类型：{job_type}")
    if job_type in ("content_draft", "checks"):
        if not content_id:
            raise PromotionError("内容任务必须绑定内容")
        content = service.get_content(content_id)
        if campaign_id is not None and content["campaign_id"] != campaign_id:
            raise PromotionError("内容不属于该活动")
        campaign_id = content["campaign_id"]
    elif not campaign_id or content_id is not None:
        raise PromotionError("活动任务必须绑定活动，不能绑定内容")
    service.get_campaign(campaign_id)
    snapshot = copy.deepcopy(_input_snapshot) if _input_snapshot is not None else generation.capture_inputs(
        job_type, campaign_id=campaign_id, content_id=content_id)
    _validate_snapshot(snapshot, job_type, campaign_id, content_id)
    saved_params = copy.deepcopy(params or {})
    saved_params["input_snapshot"] = snapshot
    conn = db.connect()
    job_id = f"job_{job_type}_{_short_id()}"
    ts = now()
    idempotency = f"{job_type}:{content_id or campaign_id}"
    # 查询和写入置于同一写事务中，避免并发请求均通过检查。
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            "SELECT id FROM generation_jobs WHERE idempotency_key=?"
            " AND status IN ('queued','running')", (idempotency,),
        ).fetchone()
        if existing is not None:
            raise PromotionError(f"已有进行中的同类任务：{existing['id']}")
        conn.execute(
            "INSERT INTO generation_jobs (id, campaign_id, content_id, job_type, status,"
            " params, idempotency_key, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (job_id, campaign_id, content_id, job_type, "queued",
             json.dumps(saved_params, ensure_ascii=False), idempotency, ts, ts),
        )
    if auto_start:
        start_job(job_id)
    return _row(job_id)


def _validate_snapshot(snapshot: dict, job_type: str,
                       campaign_id: str, content_id: str | None) -> None:
    if not isinstance(snapshot, dict) or snapshot.get("schema_version") != 1:
        raise PromotionError("任务缺少有效输入快照，请重新创建任务")
    if job_type == "checks":
        content = snapshot.get("content") or {}
        revision = snapshot.get("revision") or {}
        if (content.get("id") != content_id or content.get("campaign_id") != campaign_id
                or revision.get("id") != content.get("revision_id")):
            raise PromotionError("检查任务与输入快照不一致")
    else:
        context = generation._snapshot_context(snapshot, campaign_id)
        campaign = service.get_campaign(campaign_id)
        product_id = campaign["product_id"]
        version = context.get("version") or {}
        if (context.get("product", {}).get("id") != product_id
                or context["campaign"].get("product_id") != product_id
                or not version.get("confirmed")
                or version.get("id") != campaign["version_id"]
                or context["campaign"].get("version_id") != campaign["version_id"]):
            raise PromotionError("任务快照不属于该产品版本")
        assets = context.get("assets") or []
        if any(asset.get("product_id") != product_id
               or asset.get("version_id") not in (None, version["id"]) for asset in assets):
            raise PromotionError("快照素材不属于该产品版本")
        content = snapshot.get("content") or {}
        if content_id is not None and (content.get("id") != content_id
                or content.get("campaign_id") != campaign_id):
            raise PromotionError("生成任务与输入快照不一致")
        if content_id is not None and set(content.get("asset_refs") or []) != {
                asset["id"] for asset in assets}:
            raise PromotionError("快照素材引用不一致")


def _short_id() -> str:
    import uuid
    return uuid.uuid4().hex[:10]


def start_job(job_id: str) -> None:
    thread = threading.Thread(target=_run_job_safe, args=(job_id,), daemon=True,
                              name=f"promotion-job-{job_id}")
    with _lock:
        _workers[job_id] = thread
    thread.start()


def _update(job_id: str, **fields) -> None:
    conn = db.connect()
    sets = ", ".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE generation_jobs SET {sets}, updated_at=? WHERE id=?",
                 (*fields.values(), now(), job_id))
    conn.commit()


def _append_step(job_id: str, step: dict, steps: list, artifacts: list) -> None:
    steps.append(step)
    if step.get("artifact"):
        artifacts.append(step["artifact"])
    _update(job_id, steps=json.dumps(steps, ensure_ascii=False),
            artifacts=json.dumps(artifacts, ensure_ascii=False))


def _run_job_safe(job_id: str) -> None:
    try:
        _run_job(job_id)
    except Exception as exc:  # noqa: BLE001 —— 错误摘要必须落库供重试诊断
        try:
            _update(job_id, status="failed",
                    error=f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=3)}",
                    finished_at=now())
        finally:
            with _lock:
                _workers.pop(job_id, None)
        return
    with _lock:
        _workers.pop(job_id, None)


def _run_job(job_id: str) -> None:
    job = _row(job_id)
    if job["status"] not in ("queued",):
        raise PromotionError(f"任务状态为 {job['status']}，不能启动")
    _update(job_id, status="running")
    steps: list = []
    artifacts: list = []
    job_type = job["job_type"]
    params = job["params"] or {}
    snapshot = params.get("input_snapshot")
    _validate_snapshot(snapshot, job_type, job["campaign_id"], job["content_id"])
    if job["content_id"]:
        latest = service.get_content(job["content_id"])["revisions"][-1]
        if latest["id"] != snapshot["content"]["revision_id"]:
            raise PromotionError("任务输入修订已变化，请重新创建任务")

    if job_type == "brief":
        _append_step(job_id, {"step": "生成活动简报"}, steps, artifacts)
        result = generation.generate_brief(job["campaign_id"], input_snapshot=snapshot)
        service.update_campaign(job["campaign_id"],
                                CampaignUpdate(brief=result["brief"]))
        _append_step(job_id, {"step": "保存简报", "ok": True}, steps, artifacts)
        _update(job_id, status="succeeded", finished_at=now(),
                artifacts=json.dumps(
                    [{"kind": "brief", "mode": result["generation_mode"]}],
                    ensure_ascii=False))
        return

    if job_type == "angles":
        _append_step(job_id, {"step": "生成宣传角度"}, steps, artifacts)
        result = generation.generate_angles(job["campaign_id"], input_snapshot=snapshot)
        _update_campaign_field(job["campaign_id"], "angles", result["angles"])
        _update(job_id, status="succeeded", finished_at=now(),
                artifacts=json.dumps(
                    [{"kind": "angles", "count": len(result["angles"]),
                      "mode": result["generation_mode"]}], ensure_ascii=False))
        return

    if job_type == "content_draft":
        content_id = job["content_id"]
        _append_step(job_id, {"step": "生成内容草稿"}, steps, artifacts)
        result = generation.generate_content(content_id, input_snapshot=snapshot)
        service.edit_content(
            content_id,
            ContentEdit(title=result["title"], body=result["body"],
                        structure=result["structure"], asset_refs=result["asset_refs"]),
            input_snapshot=snapshot,
            expected_revision_id=snapshot["content"]["revision_id"],
        )
        _append_step(job_id, {
            "step": "写入新修订", "ok": True,
            "artifact": {"kind": "revision", "mode": result["generation_mode"]},
        }, steps, artifacts)
        _update(job_id, status="succeeded", finished_at=now())
        return

    if job_type == "checks":
        content_id = job["content_id"]
        content = service.get_content(content_id)
        revision = snapshot["revision"]
        issues = run_checks(content, revision)
        service.record_check(content_id, issues, revision_id=revision["id"])
        _update(job_id, status="succeeded", finished_at=now(),
                artifacts=json.dumps([{"kind": "checks", "count": len(issues)}],
                                     ensure_ascii=False))
        return

    raise PromotionError(f"未实现的任务类型：{job_type}")


def _update_campaign_field(campaign_id: str, field: str, value) -> None:
    conn = db.connect()
    conn.execute(
        f"UPDATE campaigns SET {field}=?, updated_at=? WHERE id=?",
        (json.dumps(value, ensure_ascii=False), now(), campaign_id),
    )
    conn.commit()



def cancel_job(job_id: str) -> dict:
    conn = db.connect()
    row = conn.execute("SELECT status FROM generation_jobs WHERE id=?", (job_id,)).fetchone()
    if row is None:
        raise PromotionError(f"任务不存在：{job_id}")
    if row["status"] in ("succeeded", "failed", "cancelled"):
        return _row(job_id)
    conn.execute(
        "UPDATE generation_jobs SET status='cancelled', finished_at=?, updated_at=?"
        " WHERE id=? AND status='queued'", (now(), now(), job_id))
    conn.commit()
    return _row(job_id)


def retry_job(job_id: str) -> dict:
    """重试失败/取消的任务：保留原任务和原输入快照，新建执行记录。

    生成任务重试只影响内容修订，不会产生发布记录（发布仅人工回填）。
    """
    job = _row(job_id)
    if job["status"] not in ("failed", "cancelled", "interrupted"):
        raise PromotionError("只有失败或已取消的任务可以重试")
    snapshot = job["params"].get("input_snapshot")
    _validate_snapshot(snapshot, job["job_type"], job["campaign_id"], job["content_id"])
    return create_job(
        job["job_type"], campaign_id=job["campaign_id"], content_id=job["content_id"],
        params=job["params"], auto_start=True, _input_snapshot=snapshot)
