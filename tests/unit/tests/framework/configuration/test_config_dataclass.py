"""Unit tests for config_dataclass module."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest import mock

import pytest
import toml

from opensage.config.config_dataclass import (
    AgentEnsembleConfig,
    BuildConfig,
    ContainerConfig,
    HistoryConfig,
    LLMConfig,
    MCPConfig,
    MCPServiceConfig,
    ModelConfig,
    Neo4jConfig,
    OpenSageConfig,
    SandboxConfig,
    _expand_template_variables,
    load_config_from_toml,
)


class TestTemplateVariableExpansion:
    """Test template variable expansion functionality."""

    def test_expand_simple_template_variable(self):
        """Test basic template variable expansion."""
        config_data = {"TEST_VAR": "test_value", "result": "${TEST_VAR}"}

        expanded = _expand_template_variables(config_data)
        assert expanded["result"] == "test_value"
        assert expanded["TEST_VAR"] == "test_value"

    def test_expand_multiple_template_variables(self):
        """Test expansion with multiple variables."""
        config_data = {
            "HOST": "localhost",
            "PORT": 8080,
            "URI": "http://${HOST}:${PORT}/api",
        }

        expanded = _expand_template_variables(config_data)
        assert expanded["URI"] == "http://localhost:8080/api"

    # def test_environment_variable_override(self):
    #     """Test environment variables override config variables."""
    #     config_data = {"TEST_VAR": "config_value", "result": "${TEST_VAR}"}

    #     with mock.patch.dict(os.environ, {"TEST_VAR": "env_value"}):
    #         expanded = _expand_template_variables(config_data)
    #         assert expanded["result"] == "env_value"

    def test_undefined_variable_raises_error(self):
        """Test that undefined variables raise KeyError."""
        config_data = {"result": "${UNDEFINED_VAR}"}

        with pytest.raises(
            KeyError, match="Template variable 'UNDEFINED_VAR' not found"
        ):
            _expand_template_variables(config_data)

    def test_type_conversion_integer(self):
        """Test automatic type conversion to integer."""
        config_data = {"PORT": "8080", "result": "${PORT}"}

        expanded = _expand_template_variables(config_data)
        assert expanded["result"] == 8080
        assert isinstance(expanded["result"], int)

    def test_type_conversion_float(self):
        """Test automatic type conversion to float."""
        config_data = {"TEMPERATURE": "0.7", "result": "${TEMPERATURE}"}

        expanded = _expand_template_variables(config_data)
        assert expanded["result"] == 0.7
        assert isinstance(expanded["result"], float)

    def test_type_conversion_boolean(self):
        """Test automatic type conversion to boolean."""
        config_data = {
            "ENABLED": "true",
            "DISABLED": "false",
            "result_true": "${ENABLED}",
            "result_false": "${DISABLED}",
        }

        expanded = _expand_template_variables(config_data)
        assert expanded["result_true"] is True
        assert expanded["result_false"] is False

    def test_nested_dict_expansion(self):
        """Test template expansion in nested dictionaries."""
        config_data = {
            "HOST": "localhost",
            "nested": {
                "uri": "http://${HOST}/api",
                "deeper": {"connection": "${HOST}:3306"},
            },
        }

        expanded = _expand_template_variables(config_data)
        assert expanded["nested"]["uri"] == "http://localhost/api"
        assert expanded["nested"]["deeper"]["connection"] == "localhost:3306"

    def test_list_expansion(self):
        """Test template expansion in lists."""
        config_data = {
            "BASE_URL": "https://api.example.com",
            "endpoints": ["${BASE_URL}/users", "${BASE_URL}/posts"],
        }

        expanded = _expand_template_variables(config_data)
        assert expanded["endpoints"] == [
            "https://api.example.com/users",
            "https://api.example.com/posts",
        ]

    def test_partial_template_replacement(self):
        """Test partial template replacement within strings."""
        config_data = {
            "PROTOCOL": "https",
            "HOST": "example.com",
            "full_url": "${PROTOCOL}://${HOST}/api/v1",
        }

        expanded = _expand_template_variables(config_data)
        assert expanded["full_url"] == "https://example.com/api/v1"


class TestDataclassCreation:
    """Test creation of various configuration dataclasses."""

    def test_neo4j_config_creation(self):
        """Test Neo4j configuration creation."""
        config = Neo4jConfig(user="neo4j", password="test_password")

        assert config.user == "neo4j"
        assert config.password == "test_password"

    def test_neo4j_config_defaults(self):
        """Test Neo4j configuration with default values."""
        config = Neo4jConfig()

        assert config.user is None
        assert config.password is None
        # URI is now dynamically constructed, should return default
        assert config.uri == "bolt://127.0.0.1:7687"
        assert config.bolt_port == 7687

    def test_container_config_creation(self):
        """Test ContainerConfig creation with various options."""
        config = ContainerConfig(
            image="ubuntu:20.04",
            timeout=300,
            environment={"TEST": "value"},
            volumes=["/host:/container"],
            privileged=True,
        )

        assert config.image == "ubuntu:20.04"
        assert config.timeout == 300
        assert config.environment == {"TEST": "value"}
        assert config.volumes == ["/host:/container"]
        assert config.privileged is True

    def test_container_config_defaults(self):
        """Test ContainerConfig default values."""
        config = ContainerConfig()

        assert config.image is None
        assert config.timeout == 300
        assert config.privileged is False
        assert config.environment == {}
        assert config.volumes == []

    def test_model_config_creation(self):
        """Test ModelConfig creation."""
        config = ModelConfig(
            model_name="test-model", temperature=0.7, max_tokens=1024, rpm=60, tpm=60000
        )

        assert config.model_name == "test-model"
        assert config.temperature == 0.7
        assert config.max_tokens == 1024
        assert config.rpm == 60
        assert config.tpm == 60000

    def test_llm_config_with_models(self):
        """Test LLMConfig with multiple models."""
        main_model = ModelConfig(model_name="main-model", temperature=0.7)
        summary_model = ModelConfig(model_name="summary-model", temperature=0.3)

        llm_config = LLMConfig(
            model_configs={"main": main_model, "summarize": summary_model}
        )

        assert llm_config.get_model_config("main") == main_model
        assert llm_config.get_model_config("summarize") == summary_model
        assert llm_config.model_name == "main-model"
        assert llm_config.summarize_model == "summary-model"

    def test_history_config_creation(self):
        """Test HistoryConfig creation."""
        config = HistoryConfig(
            max_tool_response_length=2000,
            events_compaction=HistoryConfig.EventsCompactionConfig(
                max_history_summary_length=80000,
                compaction_percent=60,
            ),
        )

        assert config.max_tool_response_length == 2000
        assert config.events_compaction.max_history_summary_length == 80000
        assert config.events_compaction.compaction_percent == 60

    def test_agent_ensemble_config_creation(self):
        """Test AgentEnsembleConfig creation."""
        config = AgentEnsembleConfig(
            thread_safe_tools={"tool1", "tool2"},
            available_models_for_ensemble=["model1", "model2"],
        )

        assert config.thread_safe_tools == {"tool1", "tool2"}
        assert config.available_models_for_ensemble == ["model1", "model2"]

    def test_build_config_creation(self):
        """Test BuildConfig creation."""
        config = BuildConfig(
            poc_dir="/tmp/poc",
            compile_command="make build",
            run_command="./app",
            target_type="executable",
        )

        assert config.poc_dir == "/tmp/poc"
        assert config.compile_command == "make build"
        assert config.run_command == "./app"
        assert config.target_type == "executable"

    def test_mcp_service_config_creation(self):
        """Test MCPServiceConfig creation."""
        config = MCPServiceConfig(sse_port=1111)

        assert config.sse_port == 1111

    def test_mcp_config_with_services(self):
        """Test MCPConfig with multiple services."""
        gdb_service = MCPServiceConfig(sse_port=1111)
        pdb_service = MCPServiceConfig(sse_port=1112)

        mcp_config = MCPConfig(
            services={"gdb_mcp": gdb_service, "pdb_mcp": pdb_service}
        )

        assert mcp_config.get_service_config("gdb_mcp") == gdb_service
        assert mcp_config.get_service_config("pdb_mcp") == pdb_service

    def test_sandbox_config_creation(self):
        """Test SandboxConfig creation."""
        main_container = ContainerConfig(image="ubuntu:20.04")
        worker_container = ContainerConfig(image="alpine:latest")

        config = SandboxConfig(
            default_image="ubuntu:20.04",
            sandboxes={"main": main_container, "worker": worker_container},
            backend="native",
        )

        assert config.default_image == "ubuntu:20.04"
        assert config.backend == "native"
        assert config.get_sandbox_config("main") == main_container
        assert config.get_sandbox_config("worker") == worker_container


class TestOpenSageConfigTOMLLoading:
    """Test TOML configuration loading and parsing."""

    def setup_method(self):
        """Set up test fixtures."""
        self.temp_dir = tempfile.mkdtemp()
        self.test_config_path = Path(self.temp_dir) / "test_config.toml"

    def teardown_method(self):
        """Clean up test fixtures."""
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_load_simple_toml_config(self):
        """Test loading a simple TOML configuration."""
        toml_content = """
# Test configuration
DEFAULT_IMAGE = "ubuntu:20.04"
NEO4J_PASSWORD = "test_password"

task_name = "test_task"

[neo4j]
user = "neo4j"
password = "${NEO4J_PASSWORD}"
bolt_port = 7687

[sandbox]
default_image = "${DEFAULT_IMAGE}"
backend = "native"

[sandbox.sandboxes.main]
image = "${DEFAULT_IMAGE}"
timeout = 300
"""

        with open(self.test_config_path, "w") as f:
            f.write(toml_content)

        config = OpenSageConfig.from_toml(str(self.test_config_path))

        assert config.task_name == "test_task"
        assert config.neo4j.user == "neo4j"
        assert config.neo4j.password == "test_password"
        # URI is now dynamically constructed from bolt_port
        assert config.neo4j.uri == "bolt://127.0.0.1:7687"
        assert config.neo4j.bolt_port == 7687
        assert config.sandbox.default_image == "ubuntu:20.04"
        assert config.sandbox.backend == "native"
        assert config.sandbox.sandboxes["main"].image == "ubuntu:20.04"
        assert config.sandbox.sandboxes["main"].timeout == 300

    def test_load_llm_config_from_toml(self):
        """Test loading LLM configuration from TOML."""
        toml_content = """
MAIN_MODEL = "test/model"

[llm.model_configs.main]
model_name = "${MAIN_MODEL}"
temperature = 0.7
max_tokens = 4096
rpm = 60
tpm = 60000

[llm.model_configs.summarize]
model_name = "${MAIN_MODEL}"
temperature = 0.3
max_tokens = 2048
"""

        with open(self.test_config_path, "w") as f:
            f.write(toml_content)

        config = OpenSageConfig.from_toml(str(self.test_config_path))

        assert config.llm is not None
        assert config.llm.model_name == "test/model"
        assert config.llm.summarize_model == "test/model"

        main_config = config.llm.get_model_config("main")
        assert main_config.model_name == "test/model"
        assert main_config.temperature == 0.7
        assert main_config.max_tokens == 4096

        summary_config = config.llm.get_model_config("summarize")
        assert summary_config.model_name == "test/model"
        assert summary_config.temperature == 0.3
        assert summary_config.max_tokens == 2048

    def test_load_agent_ensemble_config_from_toml(self):
        """Test loading agent ensemble configuration from TOML."""
        toml_content = """
[agent_ensemble]
thread_safe_tools = ["tool1", "tool2"]
available_models_for_ensemble = "model1,model2,model3"
"""

        with open(self.test_config_path, "w") as f:
            f.write(toml_content)

        config = OpenSageConfig.from_toml(str(self.test_config_path))

        assert config.agent_ensemble is not None
        assert config.agent_ensemble.thread_safe_tools == {"tool1", "tool2"}
        assert config.agent_ensemble.available_models_for_ensemble == [
            "model1",
            "model2",
            "model3",
        ]

    def test_load_build_config_with_empty_strings(self):
        """Test loading build configuration with empty string handling."""
        toml_content = """
[build]
poc_dir = "/tmp/poc"
compile_command = ""
run_command = "arvo"
target_type = "default"
"""

        with open(self.test_config_path, "w") as f:
            f.write(toml_content)

        config = OpenSageConfig.from_toml(str(self.test_config_path))

        assert config.build is not None
        assert config.build.poc_dir == "/tmp/poc"
        assert config.build.compile_command is None  # Empty string converted to None
        assert config.build.run_command == "arvo"
        assert config.build.target_type == "default"

    def test_load_sandbox_host_shared_mem_dir_empty_string(self):
        """Test sandbox.host_shared_mem_dir empty string is normalized to None."""
        toml_content = """
[sandbox]
backend = "native"
host_shared_mem_dir = ""

[sandbox.sandboxes.main]
image = "ubuntu:20.04"
"""
        with open(self.test_config_path, "w") as f:
            f.write(toml_content)

        config = OpenSageConfig.from_toml(str(self.test_config_path))
        assert config.sandbox is not None
        assert config.sandbox.host_shared_mem_dir is None

    def test_load_mcp_config_from_toml(self):
        """Test loading MCP configuration from TOML."""
        toml_content = """
DEFAULT_HOST = "127.0.0.1"

[mcp.services.gdb_mcp]
sse_port = 1111

[mcp.services.pdb_mcp]
sse_port = 1112
"""

        with open(self.test_config_path, "w") as f:
            f.write(toml_content)

        config = OpenSageConfig.from_toml(str(self.test_config_path))

        assert config.mcp is not None

        gdb_service = config.mcp.get_service_config("gdb_mcp")
        assert gdb_service.sse_port == 1111

        pdb_service = config.mcp.get_service_config("pdb_mcp")
        assert pdb_service.sse_port == 1112

    def test_file_not_found_error(self):
        """Test that FileNotFoundError is raised for non-existent config file."""
        with pytest.raises(FileNotFoundError, match="Configuration file not found"):
            OpenSageConfig.from_toml("/non/existent/path.toml")


#     def test_environment_variable_override_in_toml(self):
#         """Test that environment variables override TOML variables."""
#         toml_content = """
# TEST_VAR = "config_value"
# result = "${TEST_VAR}"
# """

#         with open(self.test_config_path, "w") as f:
#             f.write(toml_content)

#         with mock.patch.dict(os.environ, {"TEST_VAR": "env_override"}):
#             config_dict = toml.load(self.test_config_path)
#             expanded = _expand_template_variables(config_dict)
#             assert expanded["result"] == "env_override"


class TestOpenSageConfigMethods:
    """Test OpenSageConfig methods and functionality."""

    def setup_method(self):
        """Set up test fixtures."""
        self.config = OpenSageConfig()

        # Set up sandbox configuration
        main_container = ContainerConfig(image="ubuntu:20.04", timeout=300)
        self.config.sandbox = SandboxConfig(
            default_image="ubuntu:20.04",
            sandboxes={"main": main_container},
            backend="native",
        )

        # Set up LLM configuration
        main_model = ModelConfig(model_name="test-model", temperature=0.7)
        self.config.llm = LLMConfig(model_configs={"main": main_model})

    def test_get_sandbox_config(self):
        """Test get_sandbox_config method."""
        sandbox_config = self.config.get_sandbox_config("main")
        assert sandbox_config is not None
        assert sandbox_config.image == "ubuntu:20.04"
        assert sandbox_config.timeout == 300

        # Test non-existent sandbox
        assert self.config.get_sandbox_config("non_existent") is None

    def test_get_sandbox_config_no_sandbox(self):
        """Test get_sandbox_config when no sandbox configuration exists."""
        config = OpenSageConfig()
        assert config.get_sandbox_config("main") is None

    def test_get_llm_config(self):
        """Test get_llm_config method."""
        llm_config = self.config.get_llm_config("main")
        assert llm_config is not None
        assert llm_config.model_name == "test-model"
        assert llm_config.temperature == 0.7

        # Test non-existent model
        assert self.config.get_llm_config("non_existent") is None

    def test_get_llm_config_no_llm(self):
        """Test get_llm_config when no LLM configuration exists."""
        config = OpenSageConfig()
        assert config.get_llm_config("main") is None

    def test_save_to_toml(self):
        """Test saving configuration to TOML file."""
        with tempfile.TemporaryDirectory() as temp_dir:
            toml_path = Path(temp_dir) / "test_save.toml"

            self.config.task_name = "test_task"
            self.config.agent_storage_path = "/tmp/storage"
            self.config.save_to_toml(str(toml_path))

            assert toml_path.exists()

            # Load and verify the saved content
            loaded_data = toml.load(toml_path)
            assert loaded_data["task_name"] == "test_task"
            assert loaded_data["agent_storage_path"] == "/tmp/storage"

    def test_copy(self):
        """Test configuration deep copy."""
        self.config.task_name = "original_task"

        copied_config = self.config.copy()

        # Verify it's a different object
        assert copied_config is not self.config
        assert copied_config.task_name == "original_task"

        # Modify original and verify copy is unchanged
        self.config.task_name = "modified_task"
        assert copied_config.task_name == "original_task"

    def test_create_default(self):
        """Test create_default class method."""
        # This test might fail if default config file doesn't exist
        # We'll just verify the method exists and returns an OpenSageConfig instance
        try:
            config = OpenSageConfig.create_default()
            assert isinstance(config, OpenSageConfig)
        except FileNotFoundError:
            # Expected if default config file doesn't exist in test environment
            pytest.skip("Default config file not found in test environment")


class TestConvenienceFunctions:
    """Test convenience functions."""

    def test_load_config_from_toml(self):
        """Test load_config_from_toml convenience function."""
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "test.toml"

            toml_content = """
task_name = "convenience_test"

[neo4j]
user = "test_user"
"""

            with open(config_path, "w") as f:
                f.write(toml_content)

            config = load_config_from_toml(str(config_path))

            assert isinstance(config, OpenSageConfig)
            assert config.task_name == "convenience_test"
            assert config.neo4j.user == "test_user"
