"""保留期与分区试算业务规则。

规则口径（全模块统一，不随跨月、跨年或时区展示变化）：

* 清理日期 = 采集时间 + 保留天数，全部按日历日做日期加减，不掺小时/时区偏移；
* 分区年龄 = 今天 - 采集时间（天数）。年龄严格大于保留天数才算超期，进入「待清理」；
* 临近到期（保留天数 - 5 < 年龄 <= 保留天数）进入「待确认」，其余只做「观察中」；
* 分区内部按采集时间明细桶逐条判断，超期桶的记录条数之和即本次可清理条数；
* 试算只读存储配置（存储类型、连接串、压缩方式、分区策略、保留天数、备份策略、
  是否启用），永远不回写配置本身，因此备份策略与分区顺序不会被试算改动；
* 每个存储配置独立试算，待清理清单只包含本配置名下分区，不会删掉别的配置的分区。
"""
from __future__ import annotations

import threading
from datetime import date, datetime, timedelta
from typing import Any

from app.store import store

TRIAL_MODULE = "storage_trial"
PARTITION_MODULE = "storage_partition"
CLEANUP_MODULE = "storage_cleanup"
BOARD_MODULE = "storage_dashboard_sync"

# 试算读取的配置字段（也是确认冲突时比对的配置快照口径）。
CONFIG_FIELDS = ["存储类型", "连接串", "压缩方式", "分区策略", "保留天数", "备份策略", "是否启用"]
# 距离清理日期不足该天数（含到期当天）的分区进入「待确认」。
CONFIRM_WINDOW_DAYS = 5

TRIAL_PENDING = "待确认"
TRIAL_RUNNING = "试算中"
TRIAL_CONFIRMED = "已确认"
TRIAL_CANCELED = "已取消"
TRIAL_SUPERSEDED = "已取消"

BOARD_WAIT_CLEAN = "待清理"
BOARD_NEED_CONFIRM = "待确认"
BOARD_WATCH = "观察中"
BOARD_CLEANED = "已清理"
BOARD_REVOKED = "已撤销"


def _today() -> date:
    return date.today()


def _parse_day(value: Any) -> date:
    return datetime.strptime(str(value), "%Y-%m-%d").date()


def _next_id(module: str) -> int:
    rows = store.rows(module)
    return max((int(row.get("id", 0)) for row in rows), default=0) + 1


def _serial(value: str, prefix: str) -> str:
    return f"{prefix}-{value:0>4}"


def _retention_of(config: dict[str, Any]) -> int:
    try:
        retention = int(config.get("保留天数"))
    except (TypeError, ValueError):
        raise ValueError("保留天数必须是正整数") from None
    if retention <= 0:
        raise ValueError("保留天数必须是正整数")
    return retention


def _partition_state(age: int, retention: int) -> str:
    """分区级状态：超期待清理、临近待确认、其余观察。"""
    if age > retention:
        return BOARD_WAIT_CLEAN
    if age > retention - CONFIRM_WINDOW_DAYS:
        return BOARD_NEED_CONFIRM
    return BOARD_WATCH


class StorageTrialService:
    """保留期试算、确认清理、取消与断点续算都收口在这一个服务里。"""

    def __init__(self) -> None:
        # FastAPI 同步接口跑在线程池里，按存储配置加锁，保证并发重复试算只生效一次。
        self._locks: dict[int, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    def _lock_for(self, storage_id: int) -> threading.Lock:
        with self._locks_guard:
            if storage_id not in self._locks:
                self._locks[storage_id] = threading.Lock()
            return self._locks[storage_id]

    # ------------------------------------------------------------------ 分区读取
    def list_partitions(self, storage_id: int) -> list[dict[str, Any]]:
        """只取本存储配置的分区，且固定按 id 排序——试算不会改变分区顺序。"""
        rows = [
            row for row in store.rows(PARTITION_MODULE)
            if row.get("存储编号") == self._storage_code(storage_id)
        ]
        return sorted(rows, key=lambda row: int(row.get("id", 0)))

    def _storage_code(self, storage_id: int) -> str | None:
        config = store.find("storage", storage_id)
        return None if config is None else config.get("存储编号")

    def _fingerprint(self, config: dict[str, Any], partitions: list[dict[str, Any]]) -> str:
        retention = _retention_of(config)
        partition_sig = "|".join(
            f"{p.get('分区编号')}@{p.get('采集时间')}#{p.get('记录条数')}" for p in partitions
        )
        return f"{config.get('存储编号')}:v{config.get('配置版本')}:r{retention}:{partition_sig}"

    # ------------------------------------------------------------------ 单行试算
    def _evaluate_partition(self, partition: dict[str, Any], retention: int) -> dict[str, Any]:
        collected = _parse_day(partition["采集时间"])
        cleanup_day = collected + timedelta(days=retention)
        age = (_today() - collected).days
        state = _partition_state(age, retention)

        buckets = partition.get("明细桶") or [
            {"采集时间": partition["采集时间"], "记录条数": partition.get("记录条数", 0)}
        ]
        bucket_rows: list[dict[str, Any]] = []
        cleanable = 0
        for bucket in buckets:
            bucket_day = _parse_day(bucket["采集时间"])
            bucket_age = (_today() - bucket_day).days
            bucket_state = _partition_state(bucket_age, retention)
            count = int(bucket.get("记录条数", 0))
            if bucket_state == BOARD_WAIT_CLEAN:
                cleanable += count
            bucket_rows.append({
                "采集时间": bucket["采集时间"],
                "记录条数": count,
                "桶状态": bucket_state,
                "清理日期": (bucket_day + timedelta(days=retention)).isoformat(),
            })
        return {
            "分区编号": partition.get("分区编号"),
            "分区id": partition.get("id"),
            "采集时间": partition["采集时间"],
            "保留天数": retention,
            "清理日期": cleanup_day.isoformat(),
            "分区年龄": age,
            "分区状态": state,
            "记录条数": int(partition.get("记录条数", 0)),
            "待清理条数": cleanable if state == BOARD_WAIT_CLEAN else 0,
            "数据量MB": partition.get("数据量MB", 0),
            "明细桶": bucket_rows,
        }

    # ------------------------------------------------------------------ 发起试算
    def run_trial(
        self,
        storage_id: int,
        *,
        partition_limit: int | None = None,
    ) -> tuple[dict[str, Any] | None, str, bool]:
        """发起一轮试算。

        partition_limit 用于模拟任务中断：只处理前 N 个分区，落「试算中」与断点游标，
        之后由 resume_trial 从上次分区继续。并发重复请求命中同一指纹时直接返回已存在
        的试算单（deduped=True），不会重复生成清理时间线。
        """
        config = store.find("storage", storage_id)
        if config is None:
            return None, f"存储配置 {storage_id} 不存在或已归档", False
        try:
            retention = _retention_of(config)
        except ValueError as exc:
            return None, str(exc), False

        lock = self._lock_for(storage_id)
        with lock:
            partitions = self.list_partitions(storage_id)
            fingerprint = self._fingerprint(config, partitions)

            for trial in reversed(store.rows(TRIAL_MODULE)):
                if (
                    trial.get("存储编号") == config.get("存储编号")
                    and trial.get("去重指纹") == fingerprint
                    and trial.get("状态") in (TRIAL_PENDING, TRIAL_RUNNING)
                ):
                    return trial, "相同配置与分区的试算已存在，重复请求只生效一次", True

            # 新配置生效：旧的未决试算一律作废，冲突时以最新确认的存储配置为准。
            self._supersede_pending(config.get("存储编号"))

            trial_id = _next_id(TRIAL_MODULE)
            trial = {
                "id": trial_id,
                "试算编号": _serial(str(trial_id), "TRIL"),
                "存储编号": config.get("存储编号"),
                "存储id": storage_id,
                "配置版本": config.get("配置版本"),
                "状态": TRIAL_RUNNING,
                "去重指纹": fingerprint,
                "配置快照": {field: config.get(field) for field in CONFIG_FIELDS},
                "时间线": [],
                "待清理清单": [],
                "汇总": {},
                "断点续算": {"已完成分区": [], "上次分区": None, "是否完成": False},
                "试算时间": datetime.now().isoformat(timespec="seconds"),
                "备注": "",
            }
            store.rows(TRIAL_MODULE).append(trial)
            self._process(trial, partitions, retention, partition_limit)
            return trial, "试算已完成" if trial["状态"] == TRIAL_PENDING else "试算中断，已记录断点，可从上次分区继续", False

    def resume_trial(self, trial_id: int) -> tuple[dict[str, Any] | None, str]:
        trial = store.find(TRIAL_MODULE, trial_id)
        if trial is None:
            return None, f"试算单 {trial_id} 不存在"
        if trial.get("状态") != TRIAL_RUNNING:
            return None, f"试算单当前状态为「{trial.get('状态')}」，无需断点续算"
        config = store.find("storage", trial["存储id"])
        if config is None:
            return None, "存储配置已不存在，无法续算"
        if config.get("配置版本") != trial.get("配置版本"):
            trial["状态"] = TRIAL_SUPERSEDED
            trial["备注"] = "存储配置已更新，试算结果作废"
            self._revoke_board(trial_id)
            return None, "存储配置已更新，请基于最新配置重新试算"
        partitions = self.list_partitions(trial["存储id"])
        with self._lock_for(trial["存储id"]):
            self._process(trial, partitions, _retention_of(config), None)
        return trial, "断点续算完成" if trial["状态"] == TRIAL_PENDING else "仍有分区未处理"

    def _process(
        self,
        trial: dict[str, Any],
        partitions: list[dict[str, Any]],
        retention: int,
        limit: int | None,
    ) -> None:
        done_ids = list(trial["断点续算"]["已完成分区"])
        pending = [p for p in partitions if int(p.get("id", 0)) not in done_ids]
        if limit is not None and limit >= 0:
            pending = pending[:limit]

        for partition in pending:
            row = self._evaluate_partition(partition, retention)
            trial["时间线"].append(row)
            done_ids.append(int(partition["id"]))
            trial["断点续算"] = {
                "已完成分区": done_ids,
                "上次分区": partition.get("分区编号"),
                "是否完成": len(done_ids) >= len(partitions),
            }

        if len(done_ids) >= len(partitions):
            self._finalize(trial, partitions)
        else:
            trial["状态"] = TRIAL_RUNNING

    def _finalize(self, trial: dict[str, Any], partitions: list[dict[str, Any]]) -> None:
        timeline = trial["时间线"]
        waiting = [row for row in timeline if row["分区状态"] == BOARD_WAIT_CLEAN]
        trial["待清理清单"] = [
            {
                "分区编号": row["分区编号"],
                "采集时间": row["采集时间"],
                "清理日期": row["清理日期"],
                "待清理条数": row["待清理条数"],
                "数据量MB": row["数据量MB"],
            }
            for row in waiting
        ]
        trial["汇总"] = {
            "分区总数": len(timeline),
            "待清理分区数": len(waiting),
            "待清理条数": sum(row["待清理条数"] for row in waiting),
            "待确认分区数": sum(1 for row in timeline if row["分区状态"] == BOARD_NEED_CONFIRM),
            "观察分区数": sum(1 for row in timeline if row["分区状态"] == BOARD_WATCH),
        }
        trial["状态"] = TRIAL_PENDING
        trial["断点续算"]["是否完成"] = True
        self._sync_board(trial, partitions)

    # ------------------------------------------------------------------ 看板同步
    def _sync_board(self, trial: dict[str, Any], partitions: list[dict[str, Any]]) -> None:
        """试算完成后把每个分区同步到采集看板工作台；同一分区只保留最新一条。"""
        board = store.rows(BOARD_MODULE)
        for row in trial["时间线"]:
            for existing in board:
                if (
                    existing.get("存储编号") == trial["存储编号"]
                    and existing.get("分区编号") == row["分区编号"]
                    and existing.get("状态") not in (BOARD_CLEANED, BOARD_REVOKED)
                ):
                    existing.update({
                        "状态": row["分区状态"],
                        "清理日期": row["清理日期"],
                        "待清理条数": row["待清理条数"],
                        "试算编号": trial["试算编号"],
                        "配置版本": trial["配置版本"],
                        "更新时间": datetime.now().isoformat(timespec="seconds"),
                    })
                    break
            else:
                sync_id = _next_id(BOARD_MODULE)
                board.append({
                    "id": sync_id,
                    "同步编号": _serial(str(sync_id), "DASH-SYNC"),
                    "来源": "存储清理试算",
                    "存储编号": trial["存储编号"],
                    "分区编号": row["分区编号"],
                    "状态": row["分区状态"],
                    "采集时间": row["采集时间"],
                    "清理日期": row["清理日期"],
                    "待清理条数": row["待清理条数"],
                    "记录条数": row["记录条数"],
                    "试算编号": trial["试算编号"],
                    "配置版本": trial["配置版本"],
                    "更新时间": datetime.now().isoformat(timespec="seconds"),
                })

    def _revoke_board(self, trial_id: int) -> None:
        trial = store.find(TRIAL_MODULE, trial_id)
        if trial is None:
            return
        for item in store.rows(BOARD_MODULE):
            if item.get("试算编号") == trial.get("试算编号") and item.get("状态") != BOARD_CLEANED:
                item["状态"] = BOARD_REVOKED
                item["更新时间"] = datetime.now().isoformat(timespec="seconds")

    def _supersede_pending(self, storage_code: str) -> None:
        for trial in store.rows(TRIAL_MODULE):
            if trial.get("存储编号") == storage_code and trial.get("状态") in (TRIAL_PENDING, TRIAL_RUNNING):
                trial["状态"] = TRIAL_SUPERSEDED
                trial["备注"] = "存储配置已更新，旧试算结果作废"
                self._revoke_board(trial["id"])

    # ------------------------------------------------------------------ 确认/取消
    def confirm_trial(self, trial_id: int) -> tuple[dict[str, Any] | None, str]:
        trial = store.find(TRIAL_MODULE, trial_id)
        if trial is None:
            return None, f"试算单 {trial_id} 不存在"
        if trial.get("状态") != TRIAL_PENDING:
            return None, f"试算单当前状态为「{trial.get('状态')}」，不能确认清理"
        config = store.find("storage", trial["存储id"])
        if config is None:
            return None, "存储配置已不存在"
        if config.get("配置版本") != trial.get("配置版本"):
            trial["状态"] = TRIAL_SUPERSEDED
            trial["备注"] = "存储配置已更新，试算结果作废"
            self._revoke_board(trial_id)
            return None, "存储配置已更新，请基于最新配置重新试算"

        cleaned_rows: list[dict[str, Any]] = []
        for item in trial["待清理清单"]:
            partition = self._find_partition(trial["存储编号"], item["分区编号"])
            if partition is None:
                continue
            clean_count = int(item["待清理条数"])
            total = int(partition.get("记录条数", 0)) or clean_count
            freed_mb = round(float(partition.get("数据量MB", 0)) * clean_count / total, 1)
            record_id = _next_id(CLEANUP_MODULE)
            record = {
                "id": record_id,
                "清理编号": _serial(str(record_id), "CLEA"),
                "试算编号": trial["试算编号"],
                "存储编号": trial["存储编号"],
                "分区编号": item["分区编号"],
                "采集时间": item["采集时间"],
                "清理日期": item["清理日期"],
                "清理条数": clean_count,
                "释放数据量MB": freed_mb,
                "备份策略": trial["配置快照"].get("备份策略"),
                "状态": BOARD_CLEANED,
                "记录时间": datetime.now().isoformat(timespec="seconds"),
            }
            store.rows(CLEANUP_MODULE).append(record)
            cleaned_rows.append(record)

            # 只删超期记录：分区还有数据就保留，清零才整体移除，分区顺序由 id 维持。
            remaining = total - clean_count
            if remaining <= 0:
                store.rows(PARTITION_MODULE).remove(partition)
            else:
                partition["记录条数"] = remaining
                partition["数据量MB"] = round(float(partition.get("数据量MB", 0)) - freed_mb, 1)
                partition["明细桶"] = [
                    bucket for bucket in partition.get("明细桶", [])
                    if _partition_state((_today() - _parse_day(bucket["采集时间"])).days,
                                        _retention_of(config)) != BOARD_WAIT_CLEAN
                ]

        for board_item in store.rows(BOARD_MODULE):
            if board_item.get("试算编号") != trial["试算编号"]:
                continue
            if board_item.get("状态") == BOARD_WAIT_CLEAN:
                board_item["状态"] = BOARD_CLEANED
            else:
                board_item["状态"] = BOARD_WATCH
            board_item["更新时间"] = datetime.now().isoformat(timespec="seconds")

        trial["状态"] = TRIAL_CONFIRMED
        trial["确认时间"] = datetime.now().isoformat(timespec="seconds")
        trial["清理记录"] = [row["清理编号"] for row in cleaned_rows]
        return trial, f"已确认清理并生成 {len(cleaned_rows)} 条历史清理记录"

    def cancel_trial(self, trial_id: int) -> tuple[dict[str, Any] | None, str]:
        trial = store.find(TRIAL_MODULE, trial_id)
        if trial is None:
            return None, f"试算单 {trial_id} 不存在"
        if trial.get("状态") not in (TRIAL_PENDING, TRIAL_RUNNING):
            return None, f"试算单当前状态为「{trial.get('状态')}」，不能取消"
        trial["状态"] = TRIAL_CANCELED
        trial["取消时间"] = datetime.now().isoformat(timespec="seconds")
        trial["备注"] = "已取消，分区与备份策略保持试算前状态"
        # 试算过程从未改写分区与配置，取消只需收回看板上的待清理条目。
        self._revoke_board(trial_id)
        return trial, "试算已取消，待清理条目撤回，分区顺序与备份策略恢复原状"

    def _find_partition(self, storage_code: str, partition_code: str) -> dict[str, Any] | None:
        for partition in store.rows(PARTITION_MODULE):
            if partition.get("存储编号") == storage_code and partition.get("分区编号") == partition_code:
                return partition
        return None

    # ------------------------------------------------------------------ 查询口径
    def list_trials(self, storage_id: int | None = None) -> list[dict[str, Any]]:
        rows = store.rows(TRIAL_MODULE)
        if storage_id is not None:
            config = store.find("storage", storage_id)
            code = None if config is None else config.get("存储编号")
            rows = [row for row in rows if row.get("存储编号") == code]
        return sorted(rows, key=lambda row: int(row.get("id", 0)), reverse=True)

    def list_cleanups(self, storage_id: int | None = None) -> list[dict[str, Any]]:
        rows = store.rows(CLEANUP_MODULE)
        if storage_id is not None:
            config = store.find("storage", storage_id)
            code = None if config is None else config.get("存储编号")
            rows = [row for row in rows if row.get("存储编号") == code]
        return sorted(rows, key=lambda row: int(row.get("id", 0)), reverse=True)

    def cleanup_board(self, status: str | None = None) -> list[dict[str, Any]]:
        rows = store.rows(BOARD_MODULE)
        if status:
            rows = [row for row in rows if row.get("状态") == status]
        return sorted(rows, key=lambda row: int(row.get("id", 0)), reverse=True)

    def board_summary(self) -> dict[str, int]:
        rows = store.rows(BOARD_MODULE)
        return {
            "待清理": sum(1 for row in rows if row.get("状态") == BOARD_WAIT_CLEAN),
            "待确认": sum(1 for row in rows if row.get("状态") == BOARD_NEED_CONFIRM),
            "观察中": sum(1 for row in rows if row.get("状态") == BOARD_WATCH),
            "已清理": sum(1 for row in rows if row.get("状态") == BOARD_CLEANED),
            "已撤销": sum(1 for row in rows if row.get("状态") == BOARD_REVOKED),
        }

    # ------------------------------------------------------------------ 配置变更
    def update_config(self, storage_id: int, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        """修改存储配置（常用于调整保留天数）；新版本生效后旧试算全部作废。"""
        config = store.find("storage", storage_id)
        if config is None:
            return None, f"存储配置 {storage_id} 不存在或已归档"
        updates = {field: values[field] for field in CONFIG_FIELDS if field in values}
        if "保留天数" in updates:
            try:
                updates["保留天数"] = _retention_of({**config, **updates})
            except ValueError as exc:
                return None, str(exc)
        before = {field: config.get(field) for field in CONFIG_FIELDS}
        config.update(updates)
        config["配置版本"] = int(config.get("配置版本", 1)) + 1
        self._supersede_pending(config.get("存储编号"))
        changed = [field for field in CONFIG_FIELDS if before.get(field) != config.get(field)]
        return config, f"存储配置已更新（版本 v{config['配置版本']}），变更字段：{'、'.join(changed) or '无'}"


trial_service = StorageTrialService()
