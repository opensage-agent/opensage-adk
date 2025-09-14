#!/usr/bin/env python3
"""
Test script for SandboxManager functionality.

This script tests the basic functionality of SandboxManager including:
- Creating sandbox instances for different session_ids
- Reusing existing sandbox instances
- Cleaning up sandbox instances
"""

import os
import sys

# Add the SecAgentFramework to the Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from aigise.extended_features.sandbox_manager import SandboxManager
from aigise.sandbox import DockerConfig


def test_port_mappings():
    """Test port mapping configurations."""
    print("Testing port mappings...")

    # Test different port mapping scenarios
    test_configs = [
        # 1. Simple port mapping
        (
            DockerConfig(image="ubuntu:20.04", ports={"8080/tcp": 9000}),
            "Simple port mapping",
        ),
        # 2. Random port mapping
        (
            DockerConfig(image="ubuntu:20.04", ports={"8080/tcp": None}),
            "Random port mapping",
        ),
        # 3. Host interface binding
        (
            DockerConfig(image="ubuntu:20.04", ports={"8080/tcp": ("127.0.0.1", 9001)}),
            "Host interface binding",
        ),
        # 4. Multiple port mapping
        (
            DockerConfig(image="ubuntu:20.04", ports={"8080/tcp": [9002, 9003]}),
            "Multiple port mapping",
        ),
        # 5. Mixed port mappings
        (
            DockerConfig(
                image="ubuntu:20.04",
                ports={
                    "8080/tcp": 9004,
                    "8081/tcp": None,
                    "8082/tcp": ("127.0.0.1", 9005),
                    "8083/tcp": [9006, 9007],
                },
            ),
            "Mixed port mappings",
        ),
    ]

    # Test with both backends
    backends = ["native", "swerex"]

    for backend in backends:
        print(f"\n=== Testing with {backend} backend ===")
        for config, test_name in test_configs:
            print(f"\nTesting {test_name} with {backend} backend...")
            try:
                session_id = (
                    f"test-ports-{backend}-{test_name.lower().replace(' ', '-')}"
                )
                sandbox = SandboxManager.get_sandbox(
                    session_id, config, backend=backend
                )
                print(
                    f"✓ Successfully created sandbox with {test_name} using {backend} backend"
                )

                # Test basic functionality to verify sandbox is working
                output, exit_code = sandbox.run_command_in_container("echo 'Port test'")
                assert exit_code == 0 and "Port test" in output, (
                    "Basic sandbox functionality check failed"
                )
                print(
                    f"✓ Sandbox with {test_name} using {backend} backend is functional"
                )

                # Cleanup after each test
                SandboxManager.cleanup_sandbox(session_id)
                print(f"✓ Cleaned up sandbox with {test_name} using {backend} backend")
            except Exception as e:
                print(f"⚠ Failed {test_name} test with {backend} backend: {e}")

    print("\n✓ All port mapping tests completed!")


def test_sandbox_manager():
    """Test basic SandboxManager functionality."""
    print("Testing SandboxManager...")

    # Prepare docker config
    docker_config = DockerConfig(image=os.getenv("IMAGE_NAME", "n132/arvo:67862-vul"))

    # Test 1: Create sandbox for session1
    print("\n1. Creating sandbox for session1...")
    try:
        session1_id = "test-session-1"
        sandbox1 = SandboxManager.get_sandbox(session1_id, docker_config)
        print(f"✓ Successfully created sandbox for {session1_id}")
        print(f"  Sandbox type: {type(sandbox1).__name__}")
        if hasattr(sandbox1, "image_name"):
            print(f"  Image name: {sandbox1.image_name}")
    except Exception as e:
        assert False, f"Failed to create sandbox for session1: {e}"

    # Test 2: Get same sandbox for session1 (should reuse)
    print("\n2. Getting sandbox for session1 again (should reuse)...")
    try:
        sandbox1_again = SandboxManager.get_sandbox(session1_id, docker_config)
        if sandbox1 is sandbox1_again:
            print("✓ Successfully reused existing sandbox")
        else:
            assert False, "Created new sandbox instead of reusing"
    except Exception as e:
        assert False, f"Failed to get sandbox again: {e}"

    # Test 3: Create sandbox for session2
    print("\n3. Creating sandbox for session2...")
    try:
        session2_id = "test-session-2"
        sandbox2 = SandboxManager.get_sandbox(session2_id, docker_config)
        print(f"✓ Successfully created sandbox for {session2_id}")
        if sandbox1 is not sandbox2:
            print("✓ Different sandboxes for different sessions")
        else:
            assert False, "Same sandbox returned for different sessions"
    except Exception as e:
        assert False, f"Failed to create sandbox for session2: {e}"

    # Test 4: Test sandbox functionality
    print("\n4. Testing sandbox functionality...")
    try:
        output, exit_code = sandbox1.run_command_in_container(
            "echo 'Hello SandboxManager'"
        )
        if exit_code == 0 and "Hello SandboxManager" in output:
            print("✓ Sandbox command execution works")
        else:
            assert False, (
                f"Sandbox command failed: exit_code={exit_code}, output={output}"
            )
    except Exception as e:
        assert False, f"Sandbox command execution failed: {e}"

    # Test 5: Cleanup sandbox
    print("\n5. Testing sandbox cleanup...")
    try:
        SandboxManager.cleanup_sandbox(session1_id)
        # Accept either key removal or empty mapping
        assert not SandboxManager._instances.get(session1_id), (
            "Sandbox still in instances after cleanup"
        )
    except Exception as e:
        assert False, f"Cleanup failed: {e}"

    # Test 6: Cleanup all sandboxes
    print("\n6. Testing cleanup all...")
    try:
        SandboxManager.cleanup_all()
        assert len(SandboxManager._instances) == 0, (
            f"Still have {len(SandboxManager._instances)} instances"
        )
    except Exception as e:
        assert False, f"Failed to cleanup all: {e}"

    print("\n✓ All tests completed!")


if __name__ == "__main__":
    print("SandboxManager Test Script")
    print("=" * 40)

    # Set required environment variables for testing
    os.environ.setdefault("IMAGE_NAME", "ubuntu:20.04")
    os.environ.setdefault("COMPILE_COMMAND", "")
    os.environ.setdefault("RUN_COMMAND", "")
    os.environ.setdefault("POC_DIR", "/tmp/poc")

    # Run port mapping tests first
    try:
        test_port_mappings()
        port_tests_success = True
    except AssertionError as e:
        print(f"\n❌ Port mapping tests failed: {e}")
        port_tests_success = False

    # Run main sandbox manager tests
    try:
        test_sandbox_manager()
        sandbox_tests_success = True
    except AssertionError as e:
        print(f"\n❌ Sandbox manager tests failed: {e}")
        sandbox_tests_success = False

    success = port_tests_success and sandbox_tests_success

    if success:
        print("\n🎉 All tests passed!")
        sys.exit(0)
    else:
        print("\n❌ Some tests failed!")
        sys.exit(1)
