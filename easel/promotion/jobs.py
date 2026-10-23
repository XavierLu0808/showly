"""生成任务编排与持久化（功能文档 4.11 / 实施方案 3.5）。

- 状态机：queued → running → succeeded / failed / cancelled。
- 任务输入快照、步骤、产物、错误摘要全部落库；重启后 running 任务标记为
  failed(interrupted) 可重试，已 succeeded 不重复执行。
- 幂等键：同一 content + job_type 的进行中任务只允许一个；生成任务重试
  不会创建发布记录（发布只能人工回填），结构上杜绝重复提交。
"""
from __future__ import annotations

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
               auto_start: bool = True) -> dict:
    if job_type not in ("brief", "angles", "content_draft", "checks"):
        raise PromotionError(f"未知任务类型：{job_type}")
    conn = db.connect()
    # 幂等：同一对象同类任务不允许并发存在进行中的
    if campaign_id or content_id:
        conditions = ["job_type=?", "status IN ('queued','running')"]
        args: list = [job_type]
        if campaign_id:
            conditions.append("campaign_id=?")
            args.append(campaign_id)
        if content_id:
            conditions.append("content_id=?")
            args.append(content_id)
        existing = conn.execute(
            f"SELECT id FROM generation_jobs WHERE {' AND '.join(conditions)}", args
        ).fetchone()
        if existing is not None:
            raise PromotionError(f"已有进行中的同类任务：{existing['id']}")
    job_id = f"job_{job_type}_{_short_id()}"
    ts = now()
    idempotency = f"{job_type}:{content_id or campaign_id}"
    conn.execute(
        "INSERT INTO generation_jobs (id, campaign_id, content_id, job_type, status,"
        " params, idempotency_key, created_at, updated_at)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        (job_id, campaign_id, content_id, job_type, "queued",
         json.dumps(params or {}, ensure_ascii=False), idempotency, ts, ts),
    )
    conn.commit()
    if auto_start:
        start_job(job_id)
    return _row(job_id)


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

    if job_type == "brief":
        _append_step(job_id, {"step": "生成活动简报"}, steps, artifacts)
        result = generation.generate_brief(job["campaign_id"])
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
        result = generation.generate_angles(job["campaign_id"])
        _update_campaign_field(job["campaign_id"], "angles", result["angles"])
        _update(job_id, status="succeeded", finished_at=now(),
                artifacts=json.dumps(
                    [{"kind": "angles", "count": len(result["angles"]),
                      "mode": result["generation_mode"]}], ensure_ascii=False))
        return

    if job_type == "content_draft":
        content_id = job["content_id"]
        _append_step(job_id, {"step": "生成内容草稿"}, steps, artifacts)
        result = generation.generate_content(content_id)
        service.edit_content(
            content_id,
            ContentEdit(title=result["title"], body=result["body"],
                        structure=result["structure"]),
        )
        _set_content_status(content_id, "ready")
        _append_step(job_id, {
            "step": "写入新修订", "ok": True,
            "artifact": {"kind": "revision", "mode": result["generation_mode"]},
        }, steps, artifacts)
        _update(job_id, status="succeeded", finished_at=now())
        return

    if job_type == "checks":
        content_id = job["content_id"]
        content = service.get_content(content_id)
        latest = content["revisions"][-1]
        issues = run_checks(content, latest)
        service.record_check(content_id, issues)
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


def _set_content_status(content_id: str, status: str) -> None:
    conn = db.connect()
    conn.execute("UPDATE content_variants SET status=?, updated_at=? WHERE id=?",
                 (status, now(), content_id))
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
    """重试失败/取消的任务：原任务标记 cancelled 留痕，按原参数新建任务。

    生成任务重试只影响内容修订，不会产生发布记录（发布仅人工回填）。
    """
    job = _row(job_id)
    if job["status"] not in ("failed", "cancelled", "interrupted"):
        raise PromotionError("只有失败或已取消的任务可以重试")
    return create_job(
        job["job_type"], campaign_id=job["campaign_id"], content_id=job["content_id"],
        params=job["params"], auto_start=True)
