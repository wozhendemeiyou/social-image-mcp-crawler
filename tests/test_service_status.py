from pathlib import Path

from social_image_mcp.config import Settings
from social_image_mcp.service import SocialImageService


class ConfiguredXhsSource:
    def statuses(self):
        return [{
            "name": "xhs-downloader",
            "configured": True,
            "mode": "external-command",
            "detail": "",
            "platforms": ["xhs"],
        }]


class EmptySources:
    def statuses(self):
        return [{
            "name": "xhs-downloader",
            "configured": False,
            "mode": "not-configured",
            "detail": "Set the source command environment variable",
            "platforms": ["xhs"],
        }]


class PartiallyVerifiedSources:
    def statuses(self):
        return [{
            "name": "gallery-dl",
            "configured": True,
            "verified": True,
            "verified_platforms": ["instagram"],
            "ready_platforms": ["instagram"],
            "mode": "native-cli",
            "detail": "",
            "platforms": ["x", "instagram"],
        }]


def _settings() -> Settings:
    return Settings(
        x_bearer_token=None,
        instagram_access_token=None,
        instagram_user_id=None,
        weibo_access_token=None,
        weibo_cookie=None,
        media_crawler_command=None,
        xhs_downloader_command=None,
        gallery_dl_binary="definitely-not-installed-gallery-dl",
        douyin_search_endpoint=None,
        douyin_item_endpoint=None,
        douyin_cookie=None,
        xhs_search_endpoint=None,
        xhs_item_endpoint=None,
        xhs_cookies=None,
        weibo_search_endpoint=None,
        weibo_item_endpoint=None,
        browser_fallback=False,
        douyin_native_skill=False,
    )


def test_platform_status_counts_configured_source_project():
    service = SocialImageService(_settings())
    service.sources = ConfiguredXhsSource()

    status = {item["platform"]: item for item in service.statuses()}["xhs"]

    assert status["configured"] is True
    assert status["mode"] == "source-project"
    assert status["source_configured"] is True
    assert status["verified"] is False
    assert status["legacy_adapter_configured"] is False
    assert status["source_projects"] == ["xhs-downloader"]


def test_platform_status_stays_false_without_source_or_legacy_adapter():
    service = SocialImageService(_settings())
    service.sources = EmptySources()

    status = {item["platform"]: item for item in service.statuses()}["xhs"]

    assert status["configured"] is False
    assert status["source_configured"] is False
    assert status["legacy_adapter_configured"] is False


def test_platform_status_uses_verified_platforms_for_multi_platform_source():
    service = SocialImageService(_settings())
    service.sources = PartiallyVerifiedSources()

    statuses = {item["platform"]: item for item in service.statuses()}

    assert statuses["instagram"]["verified"] is True
    assert statuses["instagram"]["ready"] is True
    assert statuses["x"]["verified"] is False


def test_default_runtime_paths_are_absolute():
    settings = Settings()
    assert Path(settings.cache_path).is_absolute()
    assert Path(settings.output_dir).is_absolute()


def test_configured_legacy_adapter_is_not_verified_before_real_request():
    settings = _settings()
    settings = Settings(**{**settings.__dict__, "x_bearer_token": "configured-token"})
    service = SocialImageService(settings)

    status = {item["platform"]: item for item in service.statuses()}["x"]

    assert status["configured"] is True
    assert status["legacy_adapter_configured"] is True
    assert status["verified"] is False
    assert status["ready"] is False
