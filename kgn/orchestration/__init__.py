"""Orchestration sub-package — multi-agent coordination.

Provides:
- ``RoleGuard`` — role-based permission enforcement
- ``WorkflowEngine`` — declarative TASK decomposition engine
- ``WorkflowTemplate`` — workflow template data class
- ``HandoffService`` — context propagation between task transitions
- ``MatchingService`` — role-based agent matching for task assignment
- ``NodeLockService`` — concurrent-access node locking (lease-based)
- ``ConflictResolutionService`` — concurrent edit detection and mediation
- ``ObservabilityService`` — agent workflow tracking and bottleneck detection
- Built-in templates via ``kgn.orchestration.templates``
"""
