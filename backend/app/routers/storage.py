"""采集数据存储接口：维护存储配置，并提供保留期与分区试算的完整操作面。

路由顺序注意：固定路径（/trials、/cleanups 等）必须写在 /{entry_id} 之前，
否则「trials」会被当成存储配置 id 解析失败。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult, TrialRunPayload
from app.services.storage import StorageService
from app.services.storage_trial import trial_service

router = APIRouter(prefix="/api/storage", tags=["采集数据存储"])

service = StorageService()

LIST_FIELDS = ["存储编号", "存储类型", "连接串", "压缩方式", "分区策略", "保留天数", "备份策略", "是否启用"]
STATUSES = ["草稿", "待校验", "已启用", "已停用"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按存储编号检索"),
    status: str | None = Query(default=None, description="草稿、待校验、已启用、已停用"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按存储编号与状态过滤采集数据存储列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条存储配置，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="存储配置已登记", entry=entry)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出采集数据存储清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "storage", "total": total, "items": items}


@router.get("/trials", response_model=dict)
def list_trials(storage_id: int | None = Query(default=None, description="按存储配置过滤")) -> dict[str, Any]:
    """试算历史：最新试算排在最前，可按存储配置收敛。"""
    rows = trial_service.list_trials(storage_id)
    return {"total": len(rows), "items": rows}


@router.get("/cleanups", response_model=dict)
def list_cleanups(storage_id: int | None = Query(default=None, description="按存储配置过滤")) -> dict[str, Any]:
    """历史清理记录：确认试算后才会产生，取消试算不留清理记录。"""
    rows = trial_service.list_cleanups(storage_id)
    return {"total": len(rows), "items": rows}


@router.get("/cleanup-board", response_model=dict)
def cleanup_board(
    status: str | None = Query(default=None, description="待清理、待确认、观察中、已清理、已撤销"),
) -> dict[str, Any]:
    """工作台待清理条目：与采集看板共用同一份同步结果。"""
    return {
        "summary": trial_service.board_summary(),
        "items": trial_service.cleanup_board(status),
    }


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条存储配置明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"存储配置 {entry_id} 不存在或已归档")
    return entry


@router.patch("/{entry_id}", response_model=ActionResult)
def update_entry(entry_id: int, payload: EntryPayload) -> ActionResult:
    """更新存储配置（典型场景：保留天数 30 改 15）；新版本生效，未决试算自动作废。"""
    entry, message = service.update_entry(entry_id, payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.get("/{entry_id}/partitions", response_model=dict)
def list_partitions(entry_id: int) -> dict[str, Any]:
    """读取存储配置名下分区，分区顺序固定，试算与取消都不会打乱它。"""
    if service.get_entry(entry_id) is None:
        raise HTTPException(status_code=404, detail=f"存储配置 {entry_id} 不存在或已归档")
    items = trial_service.list_partitions(entry_id)
    return {"storage_id": entry_id, "total": len(items), "items": items}


@router.post("/{entry_id}/trials", response_model=ActionResult)
def run_trial(entry_id: int, payload: TrialRunPayload | None = None) -> ActionResult:
    """发起保留期与分区试算：生成清理时间线与待清理清单，并同步到采集看板工作台。"""
    limit = payload.partition_limit if payload else None
    trial, message, deduped = trial_service.run_trial(entry_id, partition_limit=limit)
    if trial is None:
        return ActionResult(ok=False, message=message)
    suffix = "（并发重复请求已去重）" if deduped else ""
    return ActionResult(ok=True, message=message + suffix, entry=trial)


@router.post("/trials/{trial_id}/resume", response_model=ActionResult)
def resume_trial(trial_id: int) -> ActionResult:
    """任务中断后续算：从上次分区继续，已完成的分区不重复计算。"""
    trial, message = trial_service.resume_trial(trial_id)
    if trial is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=trial)


@router.post("/trials/{trial_id}/confirm", response_model=ActionResult)
def confirm_trial(trial_id: int) -> ActionResult:
    """确认清理：只删超期记录，写入历史清理记录并同步看板；配置已变更时拒绝确认。"""
    trial, message = trial_service.confirm_trial(trial_id)
    if trial is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=trial)


@router.post("/trials/{trial_id}/cancel", response_model=ActionResult)
def cancel_trial(trial_id: int) -> ActionResult:
    """取消试算：撤回看板待清理条目，分区顺序与备份策略恢复试算前状态。"""
    trial, message = trial_service.cancel_trial(trial_id)
    if trial is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=trial)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条存储配置执行提交校验、启用配置、停用配置；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
