import json

import pytest

from schema_engine.config import (
    PRESETS,
    ConfigError,
    Settings,
    config_path,
    load_settings,
    parse_dotenv,
    redact,
    require_key,
)


def settings_from(tmp_path, env=None, dotenv="", config=None, **overrides):
    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text(dotenv, encoding="utf-8")
    config_file = tmp_path / "config.json"
    if config is not None:
        config_file.write_text(json.dumps(config), encoding="utf-8")
    return load_settings(
        env=env or {}, dotenv_path=dotenv_path, config_file=config_file, **overrides
    )


def test_defaults_to_local_ollama(tmp_path):
    s = settings_from(tmp_path)
    assert (s.provider, s.model, s.base_url, s.api_key) == (
        "ollama",
        "llama3.1",
        "http://localhost:11434",
        None,
    )


def test_presets_for_cloud_providers(tmp_path):
    openai = settings_from(tmp_path, env={"OPENAI_API_KEY": "sk-o"}, provider="openai")
    groq = settings_from(tmp_path, env={"GROQ_API_KEY": "gsk-g", "SCHEMA_ENGINE_PROVIDER": "GROQ"})
    assert (openai.model, openai.base_url, openai.api_key) == (
        "gpt-6-luna",
        "https://api.openai.com/v1",
        "sk-o",
    )
    assert (groq.provider, groq.model, groq.api_key) == (
        "groq",
        "llama-3.3-70b-versatile",
        "gsk-g",
    )


def test_override_beats_env_beats_dotenv_beats_config_file(tmp_path):
    config = {"provider": "groq", "model": "from-file", "providers": {"groq": {"api_key": "f"}}}
    dotenv = "SCHEMA_ENGINE_MODEL=from-dotenv\nGROQ_API_KEY=d\n"
    only_file = settings_from(tmp_path, config=config)
    assert (only_file.provider, only_file.model, only_file.api_key) == ("groq", "from-file", "f")

    with_dotenv = settings_from(tmp_path, dotenv=dotenv, config=config)
    assert (with_dotenv.model, with_dotenv.api_key) == ("from-dotenv", "d")

    env = {"SCHEMA_ENGINE_MODEL": "from-env", "GROQ_API_KEY": "e"}
    with_env = settings_from(tmp_path, env=env, dotenv=dotenv, config=config)
    assert (with_env.model, with_env.api_key) == ("from-env", "e")

    flags = settings_from(
        tmp_path, env=env, dotenv=dotenv, config=config, model="from-flag", api_key="x"
    )
    assert (flags.model, flags.api_key) == ("from-flag", "x")


def test_provider_section_model_beats_top_level_model(tmp_path):
    config = {"model": "top", "providers": {"ollama": {"model": "section"}}}
    assert settings_from(tmp_path, config=config).model == "section"


def test_base_url_sources(tmp_path):
    assert settings_from(tmp_path, base_url="http://x:1/").base_url == "http://x:1"
    env = {"OLLAMA_BASE_URL": "http://gpu-box:11434"}
    assert settings_from(tmp_path, env=env).base_url == "http://gpu-box:11434"
    # OLLAMA_BASE_URL must not redirect a cloud provider.
    groq = settings_from(tmp_path, env={**env, "GROQ_API_KEY": "k"}, provider="groq")
    assert groq.base_url == "https://api.groq.com/openai/v1"
    proxy = settings_from(tmp_path, env={"SCHEMA_ENGINE_BASE_URL": "http://proxy/v1"})
    assert proxy.base_url == "http://proxy/v1"


def test_timeout_from_env_and_file(tmp_path):
    assert settings_from(tmp_path, env={"SCHEMA_ENGINE_TIMEOUT": "5"}).timeout == 5.0
    assert settings_from(tmp_path, config={"timeout": 12}).timeout == 12.0
    with pytest.raises(ConfigError, match="Invalid timeout"):
        settings_from(tmp_path, env={"SCHEMA_ENGINE_TIMEOUT": "soon"})


def test_unknown_provider(tmp_path):
    with pytest.raises(ConfigError, match="Unknown provider 'nope'"):
        settings_from(tmp_path, provider="nope")


def test_bad_config_file(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ConfigError, match="not valid JSON"):
        load_settings(env={}, dotenv_path=None, config_file=path)
    path.write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(ConfigError, match="JSON object"):
        load_settings(env={}, dotenv_path=None, config_file=path)


def test_missing_dotenv_is_fine(tmp_path):
    s = load_settings(env={}, dotenv_path=tmp_path / "nope.env", config_file=tmp_path / "x")
    assert s.provider == "ollama"


def test_parse_dotenv():
    text = """
# comment
export OPENAI_API_KEY="sk-quoted"
GROQ_API_KEY='single'
SCHEMA_ENGINE_MODEL=llama3.1 # trailing comment
HASH_INSIDE=abc#def
not a pair
=no-key
EMPTY=
"""
    assert parse_dotenv(text) == {
        "OPENAI_API_KEY": "sk-quoted",
        "GROQ_API_KEY": "single",
        "SCHEMA_ENGINE_MODEL": "llama3.1",
        "HASH_INSIDE": "abc#def",
        "EMPTY": "",
    }


def test_redact_and_repr_never_show_the_key():
    assert redact(None) is None
    assert redact("") is None
    assert redact("short") == "***"
    assert redact("sk-1234567890abcd") == "sk-…abcd"
    s = Settings("openai", "m", "https://api.openai.com/v1", "sk-1234567890abcd")
    assert "1234567890" not in repr(s)
    assert "1234567890" not in str(s.redacted())


def test_require_key(tmp_path):
    with pytest.raises(ConfigError, match="OPENAI_API_KEY"):
        require_key(Settings("openai", "m", "u", None))
    assert require_key(Settings("openai", "m", "u", "sk-x")) == "sk-x"
    assert require_key(Settings("ollama", "m", "u", None)) is None


def test_config_path_honours_xdg(tmp_path):
    path = config_path({"XDG_CONFIG_HOME": str(tmp_path)})
    assert path == tmp_path / "schema-engine" / "config.json"
    assert config_path({}).parts[-3:] == (".config", "schema-engine", "config.json")


def test_temperature_per_provider():
    assert PRESETS["ollama"].temperature == 0.0
    assert PRESETS["groq"].temperature == 0.0
    assert PRESETS["openai"].temperature is None
