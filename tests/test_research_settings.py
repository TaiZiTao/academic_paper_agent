"""文献检索 Agent 配置项默认值测试。"""

from app.config.settings import Settings


def test_research_settings_defaults():
    s = Settings(_env_file=None)
    assert s.research_top_k == 20
    assert s.research_search_timeout == 15.0
    assert s.research_download_delay == 4.0
    assert s.research_proxy == ""
    assert s.vpn_portal_url == "https://vpn.swjtu.edu.cn"
    assert s.unpaywall_email == ""
