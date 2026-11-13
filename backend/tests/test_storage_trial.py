"""保留期与分区试算的验收序列测试。

覆盖：
- 验收序列 N：STOR-0002 保留 30→15 后 P-15/P-30 清理日期提前，取消后恢复；
- 边界：P-30 共 120 条、保留 15 天清理 40 条；P-15 先进入待确认；
- STOR-0001 的 P-15（20 条）只观察，备份策略不随试算变化；
- 多配置隔离：确认 STOR-0002 不删除 STOR-0001 的分区；
- 断点续算、并发重复试算去重、看板与历史清理记录同步、配置冲突拒绝确认。
"""
from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.seed import _STORAGE_PARTITIONS, _STORAGE_ROWS
from app.store import store


@pytest.fixture()
def client() -> TestClient:
    """每个用例重置存储相关表，保证互不污染。"""
    store.rows("storage").clear()
    store.rows("storage").extend(copy.deepcopy(_STORAGE_ROWS))
    store.rows("storage_partition").clear()
    store.rows("storage_partition").extend(copy.deepcopy(_STORAGE_PARTITIONS))
    for module in ("storage_trial", "storage_cleanup", "storage_dashboard_sync"):
        store.rows(module).clear()
    return TestClient(app)


def _storage(client: TestClient, code: str) -> dict:
    rows = client.get("/api/storage").json()["items"]
    return next(row for row in rows if row["存储编号"] == code)


def _timeline(trial: dict, partition_code: str) -> dict:
    return next(row for row in trial["entry"]["时间线"] if row["分区编号"] == partition_code)


def _run_trial(client: TestClient, storage_id: int, **payload) -> dict:
    return client.post(f"/api/storage/{storage_id}/trials", json=payload).json()


def test_seed_partition_counts(client: TestClient) -> None:
    """种子数据本身满足验收：STOR-0002 P-30 共 120 条，STOR-0001 P-15 共 20 条。"""
    parts = client.get("/api/storage/2/partitions").json()["items"]
    p30 = next(p for p in parts if p["分区编号"] == "P-30")
    assert p30["记录条数"] == 120
    assert sum(b["记录条数"] for b in p30["明细桶"]) == 120

    parts1 = client.get("/api/storage/1/partitions").json()["items"]
    p15 = next(p for p in parts1 if p["分区编号"] == "P-15")
    assert p15["记录条数"] == 20


def test_trial_at_30_days_baseline(client: TestClient) -> None:
    """保留 30 天时：P-30 临近到期（待确认），P-15 观察中，均无待清理条数。"""
    trial = _run_trial(client, 2)
    assert trial["ok"]
    p15 = _timeline(trial, "P-15")
    p30 = _timeline(trial, "P-30")
    assert p15["分区状态"] == "观察中"
    assert p30["分区状态"] == "待确认"
    assert p30["待清理条数"] == 0
    # 清理日期 = 采集时间 + 保留天数，跨月按同一日期加法。
    assert p30["清理日期"] == (date.today() - timedelta(days=30) + timedelta(days=30)).isoformat()


def test_acceptance_sequence_n_retention_change_and_cancel(client: TestClient) -> None:
    """验收序列 N：30→15 后两个分区清理日期提前，取消后备份策略与分区顺序恢复。"""
    before = _run_trial(client, 2)
    p15_before = _timeline(before, "P-15")["清理日期"]
    p30_before = _timeline(before, "P-30")["清理日期"]

    resp = client.patch("/api/storage/2", json={"values": {"保留天数": 15}})
    assert resp.json()["ok"]
    updated = _storage(client, "STOR-0002")
    assert updated["保留天数"] == 15
    assert updated["配置版本"] == 2
    # 备份策略不随保留天数调整而变化。
    assert updated["备份策略"] == "每周全量备份"
    # 旧试算作废、看板条目撤销。
    old = client.get("/api/storage/trials", params={"storage_id": 2}).json()["items"]
    assert all(row["状态"] == "已取消" for row in old)

    trial = _run_trial(client, 2)
    p15 = _timeline(trial, "P-15")
    p30 = _timeline(trial, "P-30")

    # 两个分区的清理日期都提前 15 天。
    assert p15["清理日期"] < p15_before
    assert p30["清理日期"] < p30_before
    assert (date.fromisoformat(p15_before) - date.fromisoformat(p15["清理日期"])).days == 15
    assert (date.fromisoformat(p30_before) - date.fromisoformat(p30["清理日期"])).days == 15

    # P-30 清理时间提前且进入待清理，P-15 先进入待确认。
    assert p30["分区状态"] == "待清理"
    assert p30["待清理条数"] == 40
    assert p15["分区状态"] == "待确认"
    assert trial["entry"]["汇总"]["待清理条数"] == 40

    # 工作台出现待清理条目，且采集看板接口读到同一份数据。
    board_storage = client.get("/api/storage/cleanup-board").json()
    board_dashboard = client.get("/api/dashboard/cleanup-board").json()
    assert board_storage["summary"] == board_dashboard["summary"]
    assert board_storage["summary"]["待清理"] >= 1

    # 取消：待清理撤回，分区顺序与备份策略保持原状。
    trial_id = trial["entry"]["id"]
    canceled = client.post(f"/api/storage/trials/{trial_id}/cancel").json()
    assert canceled["ok"]
    board = client.get("/api/storage/cleanup-board").json()["items"]
    active = [b for b in board if b["试算编号"] == trial["entry"]["试算编号"]]
    assert all(b["状态"] == "已撤销" for b in active)

    parts = client.get("/api/storage/2/partitions").json()["items"]
    assert [p["分区编号"] for p in parts] == ["P-15", "P-30"]
    assert _storage(client, "STOR-0002")["备份策略"] == "每周全量备份"
    # 取消不产生历史清理记录。
    assert client.get("/api/storage/cleanups").json()["total"] == 0


def test_stor_0001_p15_watch_only_and_backup_policy_untouched(client: TestClient) -> None:
    """STOR-0001 的 P-15 只进入观察；试算前后备份策略不变。"""
    config_before = _storage(client, "STOR-0001")
    trial = _run_trial(client, 1)
    p15 = _timeline(trial, "P-15")
    assert p15["分区状态"] == "观察中"
    assert p15["待清理条数"] == 0
    assert trial["entry"]["汇总"]["待清理分区数"] == 0

    config_after = _storage(client, "STOR-0001")
    assert config_after["备份策略"] == config_before["备份策略"] == "跨区域冷备"
    assert config_after["配置版本"] == 1


def test_multi_partition_isolation_confirm_keeps_other_storage(client: TestClient) -> None:
    """多分区边界：两个配置独立试算，确认 STOR-0002 不会动 STOR-0001 的分区。"""
    trial1 = _run_trial(client, 1)
    client.patch("/api/storage/2", json={"values": {"保留天数": 15}})
    trial2 = _run_trial(client, 2)

    # 试算只包含本配置名下分区，天然不会触碰 STOR-0001 的分区。
    assert {row["分区id"] for row in trial2["entry"]["时间线"]} == {
        p["id"] for p in client.get("/api/storage/2/partitions").json()["items"]
    }
    assert trial1["entry"]["试算编号"] != trial2["entry"]["试算编号"]

    result = client.post(f"/api/storage/trials/{trial2['entry']['id']}/confirm").json()
    assert result["ok"]

    # STOR-0002 的 P-30 只删 40 条超期记录，分区保留且剩 80 条。
    parts2 = client.get("/api/storage/2/partitions").json()["items"]
    p30 = next(p for p in parts2 if p["分区编号"] == "P-30")
    assert p30["记录条数"] == 80
    # STOR-0001 的 P-15 原样保留 20 条。
    parts1 = client.get("/api/storage/1/partitions").json()["items"]
    assert len(parts1) == 1
    assert parts1[0]["分区编号"] == "P-15"
    assert parts1[0]["记录条数"] == 20

    # 历史清理记录与看板同步。
    cleanups = client.get("/api/storage/cleanups").json()["items"]
    assert len(cleanups) == 1
    record = cleanups[0]
    assert record["存储编号"] == "STOR-0002"
    assert record["分区编号"] == "P-30"
    assert record["清理条数"] == 40
    assert record["备份策略"] == "每周全量备份"

    board = client.get("/api/dashboard/cleanup-board", params={"status": "已清理"}).json()
    assert any(item["分区编号"] == "P-30" for item in board["items"])
    # STOR-0001 试算未确认，看板上它的 P-15 仍是观察中。
    watch = client.get("/api/dashboard/cleanup-board", params={"status": "观察中"}).json()["items"]
    assert any(item["存储编号"] == "STOR-0001" and item["分区编号"] == "P-15" for item in watch)


def test_resume_from_last_partition(client: TestClient) -> None:
    """任务中断：limit=1 先处理一个分区，续算从上次分区继续，不重复计算。"""
    interrupted = _run_trial(client, 2, partition_limit=1)
    entry = interrupted["entry"]
    assert entry["状态"] == "试算中"
    assert entry["断点续算"]["上次分区"] == "P-15"
    assert entry["断点续算"]["已完成分区"] == [2]
    assert entry["断点续算"]["是否完成"] is False
    assert len(entry["时间线"]) == 1

    resumed = client.post(f"/api/storage/trials/{entry['id']}/resume").json()
    assert resumed["ok"]
    assert resumed["entry"]["状态"] == "待确认"
    assert resumed["entry"]["断点续算"]["是否完成"] is True
    assert [row["分区编号"] for row in resumed["entry"]["时间线"]] == ["P-15", "P-30"]
    # 续算后看板条目齐全。
    board = client.get("/api/storage/cleanup-board").json()["items"]
    assert {item["分区编号"] for item in board} == {"P-15", "P-30"}


def test_concurrent_duplicate_trial_runs_once(client: TestClient) -> None:
    """并发重复试算：相同指纹只生效一次。"""
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(
            lambda _: client.post("/api/storage/3/trials", json={}),
            range(4),
        ))
    assert all(resp.json()["ok"] for resp in responses)
    trials = client.get("/api/storage/trials", params={"storage_id": 3}).json()["items"]
    active = [row for row in trials if row["状态"] == "待确认"]
    assert len(active) == 1
    # 再来一次串行请求同样命中去重。
    again = _run_trial(client, 3)
    assert "只生效一次" in again["message"]


def test_confirm_rejected_after_config_changed(client: TestClient) -> None:
    """冲突时以最新确认的存储配置为准：试算后改配置，旧试算立即作废且无法确认。"""
    trial = _run_trial(client, 3)
    trial_id = trial["entry"]["id"]
    client.patch("/api/storage/3", json={"values": {"保留天数": 10}})
    # 更新配置的瞬间旧试算已被标记作废，确认动作必须被拒绝。
    result = client.post(f"/api/storage/trials/{trial_id}/confirm").json()
    assert result["ok"] is False
    assert "不能确认" in result["message"]
    # 没有产生任何清理记录。
    assert client.get("/api/storage/cleanups").json()["total"] == 0
    # 必须基于最新配置重新试算，新一轮试算携带新版本号。
    new_trial = _run_trial(client, 3)
    assert new_trial["entry"]["配置版本"] == 2


def test_cross_month_cleanup_date_uses_one_rule(client: TestClient) -> None:
    """跨月/跨年只做日历日加法，规则唯一。"""
    client.patch("/api/storage/2", json={"values": {"保留天数": 15}})
    trial = _run_trial(client, 2)
    p30 = _timeline(trial, "P-30")
    collected = date.fromisoformat(p30["采集时间"])
    assert p30["清理日期"] == (collected + timedelta(days=15)).isoformat()
    # 明细桶横跨 10 月与 11 月，跨月桶的清理日期同样只做日历日加法，不另搞规则。
    bucket_months = {date.fromisoformat(b["采集时间"]).month for b in p30["明细桶"]}
    assert bucket_months == {10, 11}
    for bucket in p30["明细桶"]:
        expected = (date.fromisoformat(bucket["采集时间"]) + timedelta(days=15)).isoformat()
        assert bucket["清理日期"] == expected


def test_invalid_retention_rejected(client: TestClient) -> None:
    resp = client.patch("/api/storage/2", json={"values": {"保留天数": 0}})
    assert resp.json()["ok"] is False
    missing = client.post("/api/storage", json={"values": {"存储类型": "对象存储"}}).json()
    assert missing["ok"] is False and "存储编号" in missing["message"]
