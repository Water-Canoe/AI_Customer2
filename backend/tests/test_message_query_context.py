from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import database
from app.services import account_center, message_workbench


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    # 仅查询临时数据库，不启动后台队列或外部平台操作。
    monkeypatch.setenv('AI_CUSTOMER_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('AI_CUSTOMER_DB', str(tmp_path / 'message-query.sqlite3'))
    database.init_db()
    from app.main import app
    return TestClient(app)


def seed_customers():
    with database.connect() as conn:
        for index, intention in enumerate(['高', '高', '中', '低']):
            account_id = conn.execute(
                "INSERT INTO user_accounts(platform,platform_user_id,nickname) VALUES('dy',?,'高、中、低都出现在昵称中')",
                (f'customer-{index}',),
            ).lastrowid
            lead_id = conn.execute(
                "INSERT INTO lead_user_accounts(account_id,follow_status,screening_status,intention) VALUES(?,'未私信','目标客户',?)",
                (account_id, intention),
            ).lastrowid
            for keyword in ['关键词A', '关键词B']:
                conn.execute("INSERT INTO lead_sources(lead_account_id,source_type,keyword) VALUES(?,'comment',?)", (lead_id, keyword))


@pytest.mark.parametrize(('intention', 'total'), [('高', 2), ('中', 1), ('低', 1)])
def test_intention_is_exact_and_counts_match_filtered_pagination(workspace, intention, total):
    seed_customers()
    params = {'keyword': '关键词A', 'status': '待私信', 'intention': intention, 'page_size': 1}
    first = workspace.get('/api/message-workbench/customers', params=params).json()
    last = workspace.get('/api/message-workbench/customers', params={**params, 'page': total}).json()
    assert first['total'] == last['total'] == total
    assert first['total_pages'] == total
    assert all(row['intention'] == intention for row in first['rows'] + last['rows'])
    assert len({row['lead_id'] for row in first['rows'] + last['rows']}) == total
    # 同一客户有两处来源时仍只计一次，来源摘要也使用当前意向范围。
    keywords = workspace.get('/api/message-workbench/keywords', params={'intention': intention}).json()
    assert all(row['customer_count'] == total for row in keywords)
    assert workspace.get('/api/message-workbench/customers', params={'intention': '高意向'}).status_code == 422


def test_query_matches_any_comma_separated_term_and_keeps_other_filters(workspace):
    # 不同来源词分别命中；其余客户验证查询与平台、状态、意向仍取交集。
    lead_ids = []
    with database.connect() as conn:
        for index, (platform, status, intention, keyword, nickname) in enumerate([
            ('dy', '未私信', '高', '电动车', 'ALPHA采购客户'),
            ('dy', '未私信', '高', '巡游花车', 'BETA采购客户'),
            ('dy', '未私信', '高', '智能音箱', '无关客户'),
            ('ks', '未私信', '高', '电动车', '其他平台客户'),
            ('dy', '已私信', '高', '电动车', '已私信客户'),
            ('dy', '未私信', '中', '巡游花车', '中意向客户'),
        ]):
            account_id = conn.execute(
                'INSERT INTO user_accounts(platform,platform_user_id,nickname) VALUES(?,?,?)',
                (platform, f'query-customer-{index}', nickname),
            ).lastrowid
            lead_id = conn.execute(
                "INSERT INTO lead_user_accounts(account_id,follow_status,screening_status,intention) VALUES(?,?,'目标客户',?)",
                (account_id, status, intention),
            ).lastrowid
            conn.execute(
                "INSERT INTO lead_sources(lead_account_id,source_type,keyword) VALUES(?,'comment',?)",
                (lead_id, keyword),
            )
            lead_ids.append(lead_id)

    params = {'platform': 'dy', 'status': '待私信', 'intention': '高', 'query': '电动车,巡游花车', 'page_size': 1}
    first = workspace.get('/api/message-workbench/customers', params=params).json()
    second = workspace.get('/api/message-workbench/customers', params={**params, 'page': 2}).json()
    assert first['total'] == second['total'] == first['total_pages'] == second['total_pages'] == 2
    assert {row['lead_id'] for row in first['rows'] + second['rows']} == set(lead_ids[:2])
    beyond = workspace.get('/api/message-workbench/customers', params={**params, 'page': 3}).json()
    assert beyond['total'] == 2 and beyond['total_pages'] == 2 and beyond['rows'] == []

    # 混用两种逗号、首尾空格及空项，不扩大查询范围；单词仍按子串且忽略大小写。
    for query, expected_ids in [
        (' ， 电动车 , , 巡游花车， ', lead_ids[:2]),
        ('动车', lead_ids[:1]),
        (' alpha ', lead_ids[:1]),
        ('， , ，', lead_ids[:3]),
    ]:
        result = workspace.get('/api/message-workbench/customers', params={**params, 'query': query, 'page_size': 20}).json()
        assert result['total'] == len(expected_ids)
        assert {row['lead_id'] for row in result['rows']} == set(expected_ids)

    # 精确来源筛选不拆词、不改为子串；逐个放开其它约束只增加对应客户。
    for overrides, expected_ids in [
        ({'keyword': '电动车'}, lead_ids[:1]),
        ({'keyword': '电动'}, []),
        ({'keyword': '电动车,巡游花车'}, []),
        ({'platform': ''}, [*lead_ids[:2], lead_ids[3]]),
        ({'status': ''}, [*lead_ids[:2], lead_ids[4]]),
        ({'intention': ''}, [*lead_ids[:2], lead_ids[5]]),
    ]:
        result = workspace.get('/api/message-workbench/customers', params={**params, **overrides, 'page_size': 20}).json()
        assert result['total'] == len(expected_ids)
        assert {row['lead_id'] for row in result['rows']} == set(expected_ids)


def test_batch_history_uses_saved_execution_account_instead_of_new_default(workspace):
    old = account_center.create_account('dy', '当时执行账号', 'service', ['message'], ['message'])
    account_center.create_account('dy', '现在默认账号', 'service', ['message'], ['message'])
    with database.connect() as conn:
        conn.execute("INSERT INTO message_batches(id,status,account_id) VALUES('historical','succeeded',?)", (old['id'],))
    history = workspace.get('/api/message-workbench/auto-message-batches', params={'batch_id': 'historical'}).json()
    assert history['selected_batch']['account_id'] == old['id']
    assert history['selected_batch']['account_name'] == '当时执行账号'
    assert history['batches'][0]['account_name'] == '当时执行账号'
    assert message_workbench.get_auto_message_batch('historical')['account_name'] == '当时执行账号'
