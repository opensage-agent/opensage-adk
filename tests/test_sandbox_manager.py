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

from aigise.sandbox import DockerConfig
from aigise.sandbox_manager import SandboxManager


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

    success = test_sandbox_manager()

    if success:
        print("\n🎉 All tests passed!")
        sys.exit(0)
    else:
        print("\n❌ Some tests failed!")
        sys.exit(1)
