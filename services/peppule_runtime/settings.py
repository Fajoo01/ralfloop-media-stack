from pydantic import BaseModel


class PeppuleSettings(BaseModel):
    amule_api_url: str = "http://10.252.14.12:8081"
    amule_api_key: str = ""
    jellyfin_url: str = "http://10.252.14.7:8096"
    jellyfin_token: str = ""
    jellyfin_check_enabled: bool = True
    omdb_api_key: str = ""
    tmdb_api_key: str = ""
    tmdb_language: str = "it-IT"
    auto_download_enabled: bool = False
    movie_auto_download_enabled: bool = False
    series_auto_download_enabled: bool = False


def settings_model():
    """Compatibility hook for old callers; no framework decorator required."""
    return PeppuleSettings
