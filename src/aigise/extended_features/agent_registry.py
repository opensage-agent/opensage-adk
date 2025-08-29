from __future__ import annotations

import copy
import logging
from typing import Any, Callable, Dict, List, Optional, Type, Union
from abc import ABC, abstractmethod

from google.adk.agents.base_agent import BaseAgent
from google.adk.agents.llm_agent import LlmAgent

logger = logging.getLogger('secagentx.extended_features.' + __name__)

# Type aliases
AgentFactory = Callable[..., BaseAgent]
AgentTemplate = Dict[str, Any]


class AgentBuilder(ABC):
    """Abstract base class for agent builders."""
    
    @abstractmethod
    def build(self, **kwargs) -> BaseAgent:
        """Build an agent instance with the given parameters."""
        pass
    
    @abstractmethod
    def get_required_params(self) -> List[str]:
        """Get list of required parameters for building this agent."""
        pass
    
    @abstractmethod
    def get_optional_params(self) -> Dict[str, Any]:
        """Get dictionary of optional parameters with default values."""
        pass


class LlmAgentBuilder(AgentBuilder):
    """Builder for LLM agents."""
    
    def __init__(self, template: Optional[AgentTemplate] = None):
        self.template = template or {}
    
    def build(self, **kwargs) -> LlmAgent:
        """Build an LLM agent with the given parameters."""
        # Merge template with provided kwargs
        params = {**self.template, **kwargs}
        
        # Validate required parameters
        required = self.get_required_params()
        missing = [param for param in required if param not in params]
        if missing:
            raise ValueError(f"Missing required parameters: {missing}")
        
        # Auto-wrap string models with LiteLlm
        if 'model' in params and isinstance(params['model'], str):
            from google.adk.models.lite_llm import LiteLlm
            params['model'] = LiteLlm(model=params['model'])
        
        return LlmAgent(**params)
    
    def get_required_params(self) -> List[str]:
        """Get required parameters for LLM agent."""
        return ['name', 'model']
    
    def get_optional_params(self) -> Dict[str, Any]:
        """Get optional parameters with defaults."""
        return {
            'description': '',
            'instruction': '',
            'tools': [],
            'sub_agents': [],
        }


class CustomAgentBuilder(AgentBuilder):
    """Builder for custom agent types."""
    
    def __init__(self, agent_class: Type[BaseAgent], template: Optional[AgentTemplate] = None):
        self.agent_class = agent_class
        self.template = template or {}
    
    def build(self, **kwargs) -> BaseAgent:
        """Build a custom agent with the given parameters."""
        params = {**self.template, **kwargs}
        return self.agent_class(**params)
    
    def get_required_params(self) -> List[str]:
        """Get required parameters based on agent class."""
        # Use model fields to determine required parameters
        model_fields = self.agent_class.model_fields
        return [
            name for name, field in model_fields.items() 
            if field.is_required() and name != 'parent_agent'
        ]
    
    def get_optional_params(self) -> Dict[str, Any]:
        """Get optional parameters with defaults."""
        model_fields = self.agent_class.model_fields
        return {
            name: field.default for name, field in model_fields.items()
            if not field.is_required() and field.default is not None
        }


class AgentRegistry:
    """Registry for managing agent builders and templates."""
    
    def __init__(self):
        self._builders: Dict[str, AgentBuilder] = {}
        self._templates: Dict[str, AgentTemplate] = {}
        self._instances: Dict[str, BaseAgent] = {}
        
        # Register default builders
        self.register_builder('llm_agent', LlmAgentBuilder())
    
    def register_builder(self, name: str, builder: AgentBuilder) -> None:
        """Register an agent builder."""
        if name in self._builders:
            logger.warning(f"Overriding existing builder '{name}'")
        self._builders[name] = builder
        logger.info(f"Registered agent builder '{name}'")
    
    def register_template(self, name: str, template: AgentTemplate) -> None:
        """Register an agent template."""
        self._templates[name] = copy.deepcopy(template)
        logger.info(f"Registered agent template '{name}'")
    
    def register_agent_class(self, name: str, agent_class: Type[BaseAgent], 
                           template: Optional[AgentTemplate] = None) -> None:
        """Register a custom agent class with optional template."""
        builder = CustomAgentBuilder(agent_class, template)
        self.register_builder(name, builder)
    
    def create_agent(self, builder_name: str, instance_name: Optional[str] = None, 
                    **kwargs) -> BaseAgent:
        """Create an agent instance using the specified builder."""
        if builder_name not in self._builders:
            raise ValueError(f"Unknown builder '{builder_name}'. Available: {list(self._builders.keys())}")
        
        builder = self._builders[builder_name]
        agent = builder.build(**kwargs)
        
        # Store instance if name provided
        if instance_name:
            self._instances[instance_name] = agent
            logger.info(f"Created and registered agent instance '{instance_name}'")
        
        return agent
    
    def create_from_template(self, template_name: str, instance_name: Optional[str] = None,
                           **kwargs) -> BaseAgent:
        """Create an agent from a registered template."""
        if template_name not in self._templates:
            raise ValueError(f"Unknown template '{template_name}'. Available: {list(self._templates.keys())}")
        
        template = self._templates[template_name]
        builder_name = template.get('builder', 'llm_agent')
        
        # Merge template with kwargs
        params = {**template, **kwargs}
        params.pop('builder', None)  # Remove builder key from params
        
        return self.create_agent(builder_name, instance_name, **params)
    
    def get_agent(self, instance_name: str) -> Optional[BaseAgent]:
        """Get a registered agent instance."""
        return self._instances.get(instance_name)
    
    def list_builders(self) -> List[str]:
        """List all registered builders."""
        return list(self._builders.keys())
    
    def list_templates(self) -> List[str]:
        """List all registered templates."""
        return list(self._templates.keys())
    
    def list_instances(self) -> List[str]:
        """List all registered agent instances."""
        return list(self._instances.keys())
    
    def get_builder_info(self, builder_name: str) -> Dict[str, Any]:
        """Get information about a builder."""
        if builder_name not in self._builders:
            raise ValueError(f"Unknown builder '{builder_name}'")
        
        builder = self._builders[builder_name]
        return {
            'name': builder_name,
            'required_params': builder.get_required_params(),
            'optional_params': builder.get_optional_params(),
        }
    
    def clone_agent(self, instance_name: str, new_name: str, **updates) -> BaseAgent:
        """Clone an existing agent with optional updates."""
        if instance_name not in self._instances:
            raise ValueError(f"Unknown agent instance '{instance_name}'")
        
        original = self._instances[instance_name]
        cloned = original.clone(update=updates)
        
        self._instances[new_name] = cloned
        logger.info(f"Cloned agent '{instance_name}' to '{new_name}'")
        
        return cloned
    
    def remove_agent(self, instance_name: str) -> bool:
        """Remove an agent instance from registry."""
        if instance_name in self._instances:
            del self._instances[instance_name]
            logger.info(f"Removed agent instance '{instance_name}'")
            return True
        return False


# Global registry instance
_global_registry = AgentRegistry()


def get_agent_registry() -> AgentRegistry:
    """Get the global agent registry instance."""
    return _global_registry


def register_agent_builder(name: str, builder: AgentBuilder) -> None:
    """Register an agent builder globally."""
    _global_registry.register_builder(name, builder)


def register_agent_template(name: str, template: AgentTemplate) -> None:
    """Register an agent template globally."""
    _global_registry.register_template(name, template)


def register_agent_class(name: str, agent_class: Type[BaseAgent], 
                        template: Optional[AgentTemplate] = None) -> None:
    """Register a custom agent class globally."""
    _global_registry.register_agent_class(name, agent_class, template)


def create_agent(builder_name: str, instance_name: Optional[str] = None, 
                **kwargs) -> BaseAgent:
    """Create an agent using the global registry."""
    return _global_registry.create_agent(builder_name, instance_name, **kwargs)


def create_agent_from_template(template_name: str, instance_name: Optional[str] = None,
                              **kwargs) -> BaseAgent:
    """Create an agent from template using the global registry."""
    return _global_registry.create_from_template(template_name, instance_name, **kwargs)


def get_agent(instance_name: str) -> Optional[BaseAgent]:
    """Get an agent instance from the global registry."""
    return _global_registry.get_agent(instance_name)
