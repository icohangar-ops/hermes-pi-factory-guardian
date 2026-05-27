"""
Hermes-Pi Factory Guardian — Learning Loop Skill ⭐

THE KEY SKILL — demonstrates Hermes Agent's core differentiator.

Records events, finds repeating patterns, creates new Hermes skills
from those patterns, refines existing skills from operator feedback,
and tracks improvement metrics. This is what makes the factory
guardian get smarter over time without manual reconfiguration.

Author: Hermes-Pi Factory Guardian Contributors
License: MIT
"""

from __future__ import annotations

import json
import logging
import math
import os
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class Event:
    """A recorded event in the factory monitoring timeline."""
    event_type: str           # "anomaly", "alert", "feedback", "state_change", etc.
    machine_id: str
    timestamp: float = field(default_factory=time.time)
    data: Dict[str, Any] = field(default_factory=dict)
    tags: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "event_type": self.event_type,
            "machine_id": self.machine_id,
            "timestamp": self.timestamp,
            "data": self.data,
            "tags": self.tags,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> Event:
        """Deserialize from dictionary."""
        return cls(
            event_type=d["event_type"],
            machine_id=d["machine_id"],
            timestamp=d.get("timestamp", time.time()),
            data=d.get("data", {}),
            tags=d.get("tags", []),
        )


@dataclass
class Pattern:
    """A detected pattern in event history."""
    pattern_id: str
    pattern_type: str         # "temporal", "correlation", "failure_precursor", "behavioral"
    description: str
    confidence: float         # 0.0 to 1.0
    machines: List[str] = field(default_factory=list)
    conditions: Dict[str, Any] = field(default_factory=dict)
    first_seen: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    occurrence_count: int = 0
    skill_generated: bool = False
    skill_name: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "pattern_id": self.pattern_id,
            "pattern_type": self.pattern_type,
            "description": self.description,
            "confidence": round(self.confidence, 4),
            "machines": self.machines,
            "conditions": self.conditions,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "occurrence_count": self.occurrence_count,
            "skill_generated": self.skill_generated,
            "skill_name": self.skill_name,
        }


@dataclass
class SkillDefinition:
    """An auto-generated Hermes skill definition."""
    name: str
    version: str = "1.0.0"
    description: str = ""
    triggers: List[Dict[str, Any]] = field(default_factory=list)
    actions: List[Dict[str, Any]] = field(default_factory=list)
    learning_behavior: str = ""
    source_pattern_id: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    refinement_count: int = 0
    feedback_score: float = 0.0  # Positive = useful, Negative = not useful

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "triggers": self.triggers,
            "actions": self.actions,
            "learning_behavior": self.learning_behavior,
            "source_pattern_id": self.source_pattern_id,
            "created_at": self.created_at,
            "refinement_count": self.refinement_count,
            "feedback_score": round(self.feedback_score, 2),
        }

    def to_hermes_yaml(self) -> str:
        """Generate a Hermes-compatible skill YAML string."""
        import yaml

        skill_dict = {
            "skill": {
                "name": self.name,
                "version": self.version,
                "description": self.description,
                "auto_generated": True,
                "source": "hermes-pi-factory-guardian learning_loop",
                "created_at": datetime.fromtimestamp(self.created_at).isoformat(),
                "triggers": self.triggers,
                "actions": self.actions,
                "learning_behavior": self.learning_behavior,
            }
        }

        # Add metadata
        meta = {
            "source_pattern_id": self.source_pattern_id,
            "refinement_count": self.refinement_count,
            "feedback_score": self.feedback_score,
        }
        skill_dict["skill"]["_metadata"] = meta

        return yaml.dump(skill_dict, default_flow_style=False, sort_keys=False)


@dataclass
class LearningStats:
    """Statistics about the agent's learning progress."""
    total_events: int = 0
    total_patterns: int = 0
    total_skills_created: int = 0
    total_skills_refined: int = 0
    days_active: int = 0
    false_positive_rate_7d: float = 0.0
    false_positive_rate_30d: float = 0.0
    false_positive_rate_initial: float = 0.0
    improvement_percentage: float = 0.0
    patterns_by_type: Dict[str, int] = field(default_factory=dict)
    most_confident_pattern: Optional[str] = None
    most_useful_skill: Optional[str] = None


class LearningManager:
    """
    The learning engine that makes Hermes-Pi Factory Guardian self-improving.

    Records events, analyzes patterns, generates new Hermes skills from
    discovered patterns, and tracks improvement metrics over time.

    Pattern Recognition Methods:
    - Temporal: Events that repeat at specific times/days
    - Correlation: Sensors that move together
    - Failure precursor: Gradual changes before incidents
    - Behavioral: Changes in operator behavior or system usage

    The learning manager persists all data to JSON for durability and
    supports knowledge export/import for fleet deployment.
    """

    PATTERN_CONFIDENCE_THRESHOLD = 0.80
    MIN_PATTERN_OCCURRENCES = 3
    ANALYSIS_WINDOW_HOURS = 168  # 7 days

    def __init__(
        self,
        storage_dir: Optional[str] = None,
        initial_fpr: float = 0.40,
    ) -> None:
        """
        Initialize the learning manager.

        Args:
            storage_dir: Directory for persistent storage.
            initial_fpr: Initial false positive rate estimate (for tracking improvement).
        """
        self.storage_dir = Path(storage_dir or "data/learning")
        self.storage_dir.mkdir(parents=True, exist_ok=True)

        self._events: List[Event] = []
        self._patterns: Dict[str, Pattern] = {}
        self._skills: Dict[str, SkillDefinition] = {}
        self._initial_fpr = initial_fpr
        self._start_time = time.time()

        # Feedback tracking
        self._feedback_log: List[Dict[str, Any]] = []

        # Load persisted data
        self._load_events()
        self._load_patterns()
        self._load_skills()

        logger.info(
            "LearningManager initialized: events=%d, patterns=%d, skills=%d",
            len(self._events), len(self._patterns), len(self._skills),
        )

    def record_event(
        self,
        event_type: str,
        machine_id: str,
        data: Optional[Dict[str, Any]] = None,
        tags: Optional[List[str]] = None,
    ) -> Event:
        """
        Record an event in the learning timeline.

        Events are the raw material for pattern recognition. Every
        sensor reading, alert, feedback response, and state change
        should be recorded.

        Args:
            event_type: Type of event (e.g., "anomaly_detected", "alert_sent", "feedback_received").
            machine_id: Machine the event relates to.
            data: Arbitrary data associated with the event.
            tags: Tags for categorization.

        Returns:
            The created Event object.
        """
        event = Event(
            event_type=event_type,
            machine_id=machine_id,
            data=data or {},
            tags=tags or [],
        )
        self._events.append(event)

        # Persist periodically (every 100 events to avoid IO overhead)
        if len(self._events) % 100 == 0:
            self._save_events()

        logger.debug(
            "Event recorded: type=%s, machine=%s, tags=%s",
            event_type, machine_id, tags or [],
        )

        return event

    def analyze_patterns(self, days: int = 7) -> List[Pattern]:
        """
        Analyze event history for repeating patterns.

        Runs multiple pattern detection strategies on the event
        history within the specified time window.

        Args:
            days: Number of days of history to analyze.

        Returns:
            List of newly detected or updated Pattern objects.
        """
        cutoff = time.time() - (days * 86400)
        recent_events = [e for e in self._events if e.timestamp >= cutoff]

        if len(recent_events) < 50:
            logger.info(
                "Not enough events for pattern analysis: %d (need 50+)",
                len(recent_events),
            )
            return []

        logger.info(
            "Analyzing patterns in %d events from last %d days...",
            len(recent_events), days,
        )

        new_patterns: List[Pattern] = []

        # Strategy 1: Temporal patterns (events at same time/day)
        temporal = self._detect_temporal_patterns(recent_events)
        new_patterns.extend(temporal)

        # Strategy 2: Correlation patterns (events that co-occur)
        correlations = self._detect_correlation_patterns(recent_events)
        new_patterns.extend(correlations)

        # Strategy 3: Failure precursor patterns (gradual changes)
        precursors = self._detect_failure_precursor_patterns(recent_events)
        new_patterns.extend(precursors)

        # Strategy 4: Behavioral patterns (unusual operator behavior)
        behavioral = self._detect_behavioral_patterns(recent_events)
        new_patterns.extend(behavioral)

        # Update existing patterns
        for pattern in new_patterns:
            existing = self._patterns.get(pattern.pattern_id)
            if existing:
                existing.occurrence_count = pattern.occurrence_count
                existing.last_seen = pattern.last_seen
                existing.confidence = min(1.0, existing.confidence + 0.05)
            else:
                self._patterns[pattern.pattern_id] = pattern

        # Auto-generate skills from high-confidence patterns
        for pattern in self._patterns.values():
            if (
                pattern.confidence >= self.PATTERN_CONFIDENCE_THRESHOLD
                and pattern.occurrence_count >= self.MIN_PATTERN_OCCURRENCES
                and not pattern.skill_generated
            ):
                skill = self.create_skill_from_pattern(pattern)
                if skill:
                    pattern.skill_generated = True
                    pattern.skill_name = skill.name

        self._save_patterns()
        self._save_skills()

        logger.info(
            "Pattern analysis complete: %d total patterns, %d new, %d skills total",
            len(self._patterns), len(new_patterns), len(self._skills),
        )

        return new_patterns

    def create_skill_from_pattern(self, pattern: Pattern) -> Optional[SkillDefinition]:
        """
        Generate a new Hermes skill from a detected pattern.

        Creates a skill YAML definition that can be registered with
        Hermes Agent. The skill contains triggers, actions, and
        learning behavior derived from the pattern.

        Args:
            pattern: The Pattern to convert to a skill.

        Returns:
            SkillDefinition if successful, None otherwise.
        """
        if pattern.confidence < self.PATTERN_CONFIDENCE_THRESHOLD:
            logger.debug(
                "Pattern confidence too low for skill generation: %s (%.2f)",
                pattern.pattern_id, pattern.confidence,
            )
            return None

        # Generate a skill name from the pattern
        skill_name = self._pattern_to_skill_name(pattern)

        # Check if skill already exists
        if skill_name in self._skills:
            logger.info("Skill already exists: %s — refining instead", skill_name)
            return self.refine_skill(skill_name, {"pattern_update": pattern.to_dict()})

        # Build triggers from pattern conditions
        triggers = self._build_triggers_from_pattern(pattern)

        # Build actions from pattern
        actions = self._build_actions_from_pattern(pattern)

        # Build description
        description = self._build_description_from_pattern(pattern)

        # Learning behavior
        learning_behavior = (
            f"Monitor this pattern ({pattern.pattern_type}) for consistency. "
            f"If the pattern changes significantly (timing drift >15 min, "
            f"value change >20%, or stops occurring for 2 consecutive cycles), "
            f"flag for operator review. Pattern confidence: {pattern.confidence:.1%}. "
            f"Based on {pattern.occurrence_count} observations."
        )

        skill = SkillDefinition(
            name=skill_name,
            description=description,
            triggers=triggers,
            actions=actions,
            learning_behavior=learning_behavior,
            source_pattern_id=pattern.pattern_id,
        )

        self._skills[skill_name] = skill
        logger.info(
            "🆕 SKILL CREATED: %s from pattern '%s' (confidence: %.1f%%)",
            skill_name, pattern.pattern_id, pattern.confidence * 100,
        )

        return skill

    def refine_skill(
        self,
        skill_name: str,
        feedback: Dict[str, Any],
    ) -> Optional[SkillDefinition]:
        """
        Refine an existing skill based on operator feedback.

        Args:
            skill_name: Name of the skill to refine.
            feedback: Feedback dictionary with adjustments.

        Returns:
            Updated SkillDefinition if found, None otherwise.
        """
        skill = self._skills.get(skill_name)
        if skill is None:
            logger.warning("Cannot refine unknown skill: %s", skill_name)
            return None

        skill.refinement_count += 1

        # Apply feedback adjustments
        if "pattern_update" in feedback:
            pattern = feedback["pattern_update"]
            skill.description = self._build_description_from_pattern(
                Pattern(**pattern) if isinstance(pattern, dict) else pattern
            )

        if "operator_rating" in feedback:
            rating = feedback["operator_rating"]  # -1 to 1
            skill.feedback_score = (
                skill.feedback_score * 0.7 + rating * 0.3  # Smoothed update
            )

        if "adjustment" in feedback:
            adj = feedback["adjustment"]
            # Allow dynamic trigger/action updates
            if "triggers" in adj:
                skill.triggers.extend(adj["triggers"])
            if "actions" in adj:
                skill.actions.extend(adj["actions"])

        self._save_skills()

        logger.info(
            "🔧 SKILL REFINED: %s (refinement #%d, feedback_score=%.2f)",
            skill_name, skill.refinement_count, skill.feedback_score,
        )

        return skill

    def get_learning_stats(self) -> LearningStats:
        """
        Get statistics about the agent's learning progress.

        Tracks false positive rate over time, pattern counts, skill
        counts, and improvement metrics.

        Returns:
            LearningStats with comprehensive metrics.
        """
        now = time.time()
        days_active = max(1, (now - self._start_time) / 86400)

        # Count events by type for the last 7 and 30 days
        events_7d = [e for e in self._events if now - e.timestamp <= 7 * 86400]
        events_30d = [e for e in self._events if now - e.timestamp <= 30 * 86400]

        # Calculate false positive rates
        fpr_7d = self._calculate_fpr(events_7d)
        fpr_30d = self._calculate_fpr(events_30d)

        # Improvement percentage
        if self._initial_fpr > 0:
            improvement = (
                (self._initial_fpr - fpr_30d) / self._initial_fpr * 100
            )
        else:
            improvement = 0.0

        # Pattern type counts
        pattern_types: Dict[str, int] = defaultdict(int)
        for p in self._patterns.values():
            pattern_types[p.pattern_type] += 1

        # Most confident pattern
        most_confident = None
        if self._patterns:
            most_confident = max(
                self._patterns.values(), key=lambda p: p.confidence
            ).pattern_id

        # Most useful skill
        most_useful = None
        if self._skills:
            most_useful = max(
                self._skills.values(), key=lambda s: s.feedback_score
            ).name

        stats = LearningStats(
            total_events=len(self._events),
            total_patterns=len(self._patterns),
            total_skills_created=len(self._skills),
            total_skills_refined=sum(s.refinement_count for s in self._skills.values()),
            days_active=int(days_active),
            false_positive_rate_7d=fpr_7d,
            false_positive_rate_30d=fpr_30d,
            false_positive_rate_initial=self._initial_fpr,
            improvement_percentage=improvement,
            patterns_by_type=dict(pattern_types),
            most_confident_pattern=most_confident,
            most_useful_skill=most_useful,
        )

        return stats

    def export_knowledge(
        self,
        output_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Export all learned knowledge to a JSON file.

        This enables:
        - Fleet deployment (train once, deploy everywhere)
        - Backup of learned intelligence
        - Transfer learning between factories

        Args:
            output_path: File path for export. Defaults to storage_dir/export.json.

        Returns:
            Dictionary with export metadata and content.
        """
        if output_path is None:
            output_path = str(self.storage_dir / "knowledge_export.json")

        export_data = {
            "export_version": "1.0.0",
            "exported_at": time.time(),
            "exported_at_iso": datetime.now().isoformat(),
            "stats": self.get_learning_stats().__dict__,
            "patterns": {
                pid: p.to_dict() for pid, p in self._patterns.items()
            },
            "skills": {
                name: s.to_dict() for name, s in self._skills.items()
            },
            "event_summary": {
                "total_events": len(self._events),
                "events_by_type": self._count_events_by_type(),
                "machines_seen": list(set(e.machine_id for e in self._events)),
            },
        }

        try:
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(export_data, f, indent=2)
            logger.info("Knowledge exported to %s", output_path)
        except OSError as e:
            logger.error("Failed to export knowledge: %s", e)

        return export_data

    def import_knowledge(self, input_path: str) -> bool:
        """
        Import learned knowledge from a JSON file.

        Args:
            input_path: Path to knowledge export JSON file.

        Returns:
            True if import was successful.
        """
        try:
            with open(input_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            # Import patterns
            for pid, pdata in data.get("patterns", {}).items():
                pattern = Pattern(
                    pattern_id=pdata["pattern_id"],
                    pattern_type=pdata["pattern_type"],
                    description=pdata["description"],
                    confidence=pdata["confidence"],
                    machines=pdata.get("machines", []),
                    conditions=pdata.get("conditions", {}),
                    first_seen=pdata.get("first_seen", time.time()),
                    last_seen=pdata.get("last_seen", time.time()),
                    occurrence_count=pdata.get("occurrence_count", 0),
                    skill_generated=pdata.get("skill_generated", False),
                    skill_name=pdata.get("skill_name"),
                )
                # Only import if we don't have it, or merge
                if pid not in self._patterns:
                    self._patterns[pid] = pattern
                else:
                    # Merge: keep higher confidence
                    if pattern.confidence > self._patterns[pid].confidence:
                        self._patterns[pid] = pattern

            # Import skills
            for name, sdata in data.get("skills", {}).items():
                skill = SkillDefinition(
                    name=sdata["name"],
                    version=sdata.get("version", "1.0.0"),
                    description=sdata.get("description", ""),
                    triggers=sdata.get("triggers", []),
                    actions=sdata.get("actions", []),
                    learning_behavior=sdata.get("learning_behavior", ""),
                    source_pattern_id=sdata.get("source_pattern_id"),
                    created_at=sdata.get("created_at", time.time()),
                    refinement_count=sdata.get("refinement_count", 0),
                    feedback_score=sdata.get("feedback_score", 0.0),
                )
                if name not in self._skills:
                    self._skills[name] = skill

            self._save_patterns()
            self._save_skills()

            logger.info(
                "Knowledge imported from %s: %d patterns, %d skills",
                input_path, len(data.get("patterns", {})),
                len(data.get("skills", {})),
            )
            return True

        except (json.JSONDecodeError, KeyError, OSError) as e:
            logger.error("Failed to import knowledge from %s: %s", input_path, e)
            return False

    # ── Pattern Detection Strategies ─────────────────────────────────

    def _detect_temporal_patterns(self, events: List[Event]) -> List[Pattern]:
        """
        Detect events that repeat at specific times of day or days of week.

        E.g., "CNC Machine 3 always runs hot on Mondays after startup"
        """
        patterns: List[Pattern] = []

        # Group anomaly events by machine + day_of_week + hour
        time_groups: Dict[Tuple[str, str, int], List[Event]] = defaultdict(list)
        for event in events:
            if event.event_type in ("anomaly_detected", "alert_sent"):
                dt = datetime.fromtimestamp(event.timestamp)
                key = (event.machine_id, dt.strftime("%A"), dt.hour)
                time_groups[key].append(event)

        for (machine_id, day, hour), group_events in time_groups.items():
            if len(group_events) < self.MIN_PATTERN_OCCURRENCES:
                continue

            # Check if events span multiple weeks (not all same day)
            dates = set(
                datetime.fromtimestamp(e.timestamp).strftime("%Y-%m-%d")
                for e in group_events
            )
            if len(dates) < 2:
                continue

            # Calculate confidence based on consistency
            confidence = min(
                1.0,
                len(group_events) / max(len(dates), 1) / 3.0,
            )
            confidence = round(confidence, 2)

            pattern = Pattern(
                pattern_id=f"temporal_{machine_id}_{day.lower()}_{hour:02d}",
                pattern_type="temporal",
                description=(
                    f"Recurring {group_events[0].event_type} on {machine_id} "
                    f"every {day} around {hour:02d}:00 (observed {len(dates)} times)"
                ),
                confidence=confidence,
                machines=[machine_id],
                conditions={
                    "day_of_week": day,
                    "hour": hour,
                    "event_type": group_events[0].event_type,
                },
                first_seen=min(e.timestamp for e in group_events),
                last_seen=max(e.timestamp for e in group_events),
                occurrence_count=len(dates),
            )
            patterns.append(pattern)

        return patterns

    def _detect_correlation_patterns(self, events: List[Event]) -> List[Pattern]:
        """
        Detect events that co-occur across machines or sensor types.

        E.g., "When CNC #1 temp rises, CNC #2 temp follows within 10 minutes"
        """
        patterns: List[Pattern] = []

        # Group events by machine
        machine_events: Dict[str, List[Event]] = defaultdict(list)
        for event in events:
            if event.event_type == "anomaly_detected":
                machine_events[event.machine_id].append(event)

        # Check for co-occurrence between machine pairs
        machine_ids = list(machine_events.keys())
        for i in range(len(machine_ids)):
            for j in range(i + 1, len(machine_ids)):
                m1, m2 = machine_ids[i], machine_ids[j]
                co_occurrences = self._count_co_occurrences(
                    machine_events[m1], machine_events[m2],
                    window_seconds=600,  # 10 minutes
                )

                if co_occurrences >= self.MIN_PATTERN_OCCURRENCES:
                    confidence = min(1.0, co_occurrences / 10.0)

                    pattern = Pattern(
                        pattern_id=f"correlation_{m1}_{m2}",
                        pattern_type="correlation",
                        description=(
                            f"Anomalies on {m1} and {m2} frequently co-occur "
                            f"within 10 minutes ({co_occurrences} times)"
                        ),
                        confidence=round(confidence, 2),
                        machines=[m1, m2],
                        conditions={
                            "window_seconds": 600,
                            "co_occurrences": co_occurrences,
                        },
                        first_seen=time.time() - 7 * 86400,
                        last_seen=time.time(),
                        occurrence_count=co_occurrences,
                    )
                    patterns.append(pattern)

        return patterns

    def _detect_failure_precursor_patterns(
        self, events: List[Event],
    ) -> List[Pattern]:
        """
        Detect gradual changes that precede confirmed incidents.

        E.g., "Vibration increases 0.3g/day for 7 days before bearing failure"
        """
        patterns: List[Pattern] = []

        # Find confirmed incidents
        incidents = [
            e for e in events
            if e.event_type == "feedback_received"
            and e.data.get("was_real", False)
        ]

        if len(incidents) < 2:
            return patterns

        for incident in incidents:
            machine_id = incident.machine_id
            # Look for anomalies in the 7 days before this incident
            incident_time = incident.timestamp
            window_start = incident_time - (7 * 86400)

            pre_incident_events = [
                e for e in events
                if e.machine_id == machine_id
                and window_start <= e.timestamp < incident_time
                and e.event_type == "anomaly_detected"
            ]

            # Check for increasing anomaly frequency (precursor)
            if len(pre_incident_events) >= 3:
                # Divide into early half and late half
                mid = len(pre_incident_events) // 2
                early = pre_incident_events[:mid]
                late = pre_incident_events[mid:]

                early_rate = len(early) / max(
                    (early[-1].timestamp - early[0].timestamp) / 86400, 0.01
                )
                late_rate = len(late) / max(
                    (late[-1].timestamp - late[0].timestamp) / 86400, 0.01
                )

                # If late rate is significantly higher, that's a precursor
                if late_rate > early_rate * 1.5:
                    confidence = min(1.0, late_rate / (early_rate * 2))

                    pattern = Pattern(
                        pattern_id=f"precursor_{machine_id}_increasing_alerts",
                        pattern_type="failure_precursor",
                        description=(
                            f"Increasing anomaly rate on {machine_id} precedes "
                            f"confirmed incidents ({early_rate:.1f}/day → "
                            f"{late_rate:.1f}/day)"
                        ),
                        confidence=round(confidence, 2),
                        machines=[machine_id],
                        conditions={
                            "early_rate": round(early_rate, 2),
                            "late_rate": round(late_rate, 2),
                            "window_days": 7,
                        },
                        first_seen=window_start,
                        last_seen=incident_time,
                        occurrence_count=len(incidents),
                    )
                    patterns.append(pattern)

        return patterns

    def _detect_behavioral_patterns(
        self, events: List[Event],
    ) -> List[Pattern]:
        """
        Detect unusual behavioral patterns.

        E.g., "Alert suppression rate spikes between 02:00-04:00 on night shift"
        """
        patterns: List[Pattern] = []

        # Group suppressed/dismissed alerts by hour
        hour_counts: Dict[int, int] = defaultdict(int)
        total_by_hour: Dict[int, int] = defaultdict(int)

        for event in events:
            if event.event_type == "feedback_received":
                hour = datetime.fromtimestamp(event.timestamp).hour
                total_by_hour[hour] += 1
                if event.data.get("was_real") is False:
                    hour_counts[hour] += 1

        # Find hours with disproportionately many false alarms
        for hour, false_count in hour_counts.items():
            total = total_by_hour[hour]
            if total >= 3:
                ratio = false_count / total
                if ratio > 0.7:  # More than 70% false alarms at this hour
                    confidence = round(ratio, 2)
                    pattern = Pattern(
                        pattern_id=f"behavioral_high_false_alarm_hour_{hour:02d}",
                        pattern_type="behavioral",
                        description=(
                            f"High false alarm rate ({ratio:.0%}) at hour {hour:02d}:00 "
                            f"({false_count} of {total} alerts dismissed)"
                        ),
                        confidence=confidence,
                        conditions={"hour": hour, "false_alarm_rate": ratio},
                        first_seen=time.time() - 7 * 86400,
                        last_seen=time.time(),
                        occurrence_count=false_count,
                    )
                    patterns.append(pattern)

        return patterns

    # ── Helper Methods ───────────────────────────────────────────────

    def _count_co_occurrences(
        self,
        events1: List[Event],
        events2: List[Event],
        window_seconds: float = 600,
    ) -> int:
        """Count how many times events from two lists occur within a time window."""
        count = 0
        for e1 in events1:
            for e2 in events2:
                if abs(e1.timestamp - e2.timestamp) <= window_seconds:
                    count += 1
                    break  # Count each e1 only once
        return count

    def _calculate_fpr(self, events: List[Event]) -> float:
        """Calculate false positive rate from events."""
        feedback_events = [
            e for e in events if e.event_type == "feedback_received"
        ]
        if not feedback_events:
            return 0.0
        false_alarms = sum(
            1 for e in feedback_events if e.data.get("was_real") is False
        )
        return false_alarms / len(feedback_events)

    def _count_events_by_type(self) -> Dict[str, int]:
        """Count events by type."""
        counts: Dict[str, int] = defaultdict(int)
        for event in self._events:
            counts[event.event_type] += 1
        return dict(counts)

    def _pattern_to_skill_name(self, pattern: Pattern) -> str:
        """Generate a Hermes-compatible skill name from a pattern."""
        base = pattern.pattern_id.replace(" ", "_").replace("-", "_")
        # Remove common prefixes
        for prefix in ("temporal_", "correlation_", "precursor_", "behavioral_"):
            if base.startswith(prefix):
                base = base[len(prefix):]
                break
        return f"auto_{base}"

    def _build_triggers_from_pattern(
        self, pattern: Pattern,
    ) -> List[Dict[str, Any]]:
        """Build Hermes skill triggers from pattern conditions."""
        triggers = []
        for key, value in pattern.conditions.items():
            if key == "day_of_week":
                triggers.append({
                    "type": "schedule",
                    "day_of_week": value,
                })
            elif key == "hour":
                triggers.append({
                    "type": "time_window",
                    "hour": value,
                })
        return triggers

    def _build_actions_from_pattern(
        self, pattern: Pattern,
    ) -> List[Dict[str, Any]]:
        """Build Hermes skill actions from pattern."""
        actions = []

        if pattern.pattern_type == "temporal":
            actions.append({
                "type": "suppress_anomaly",
                "machines": pattern.machines,
                "conditions": pattern.conditions,
                "confidence_required": pattern.confidence,
            })
        elif pattern.pattern_type == "failure_precursor":
            actions.append({
                "type": "predictive_alert",
                "machines": pattern.machines,
                "description": f"Potential precursor detected: {pattern.description}",
                "lead_time_days": pattern.conditions.get("window_days", 7),
            })
        elif pattern.pattern_type == "behavioral":
            actions.append({
                "type": "adjust_thresholds",
                "conditions": pattern.conditions,
                "description": f"Auto-adjusting for behavioral pattern: {pattern.description}",
            })

        return actions

    def _build_description_from_pattern(self, pattern: Pattern) -> str:
        """Build a human-readable description from pattern."""
        desc = (
            f"Auto-generated skill based on {pattern.pattern_type} pattern "
            f"(confidence: {pattern.confidence:.1%}).\n\n"
            f"{pattern.description}\n\n"
            f"Machines: {', '.join(pattern.machines)}\n"
            f"Observed {pattern.occurrence_count} times since "
            f"{datetime.fromtimestamp(pattern.first_seen).strftime('%Y-%m-%d')}."
        )
        return desc

    # ── Persistence Methods ──────────────────────────────────────────

    def _save_events(self) -> None:
        """Persist events to JSON."""
        path = self.storage_dir / "events.json"
        try:
            # Only save last 10000 events to manage file size
            events_to_save = self._events[-10000:]
            data = {
                "total_events": len(self._events),
                "saved_events": len(events_to_save),
                "events": [e.to_dict() for e in events_to_save],
            }
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f)
        except OSError as e:
            logger.error("Failed to save events: %s", e)

    def _load_events(self) -> None:
        """Load events from JSON."""
        path = self.storage_dir / "events.json"
        if not path.exists():
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for ed in data.get("events", []):
                self._events.append(Event.from_dict(ed))
            logger.info("Loaded %d events from %s", len(self._events), path)
        except (json.JSONDecodeError, KeyError) as e:
            logger.error("Failed to load events: %s", e)

    def _save_patterns(self) -> None:
        """Persist patterns to JSON."""
        path = self.storage_dir / "patterns.json"
        try:
            data = {
                "patterns": {pid: p.to_dict() for pid, p in self._patterns.items()},
            }
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except OSError as e:
            logger.error("Failed to save patterns: %s", e)

    def _load_patterns(self) -> None:
        """Load patterns from JSON."""
        path = self.storage_dir / "patterns.json"
        if not path.exists():
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for pid, pdata in data.get("patterns", {}).items():
                self._patterns[pid] = Pattern(
                    pattern_id=pdata["pattern_id"],
                    pattern_type=pdata["pattern_type"],
                    description=pdata["description"],
                    confidence=pdata["confidence"],
                    machines=pdata.get("machines", []),
                    conditions=pdata.get("conditions", {}),
                    first_seen=pdata.get("first_seen", time.time()),
                    last_seen=pdata.get("last_seen", time.time()),
                    occurrence_count=pdata.get("occurrence_count", 0),
                    skill_generated=pdata.get("skill_generated", False),
                    skill_name=pdata.get("skill_name"),
                )
            logger.info("Loaded %d patterns from %s", len(self._patterns), path)
        except (json.JSONDecodeError, KeyError) as e:
            logger.error("Failed to load patterns: %s", e)

    def _save_skills(self) -> None:
        """Persist skills to JSON."""
        path = self.storage_dir / "skills.json"
        try:
            data = {
                "skills": {name: s.to_dict() for name, s in self._skills.items()},
            }
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except OSError as e:
            logger.error("Failed to save skills: %s", e)

    def _load_skills(self) -> None:
        """Load skills from JSON."""
        path = self.storage_dir / "skills.json"
        if not path.exists():
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for name, sdata in data.get("skills", {}).items():
                self._skills[name] = SkillDefinition(
                    name=sdata["name"],
                    version=sdata.get("version", "1.0.0"),
                    description=sdata.get("description", ""),
                    triggers=sdata.get("triggers", []),
                    actions=sdata.get("actions", []),
                    learning_behavior=sdata.get("learning_behavior", ""),
                    source_pattern_id=sdata.get("source_pattern_id"),
                    created_at=sdata.get("created_at", time.time()),
                    refinement_count=sdata.get("refinement_count", 0),
                    feedback_score=sdata.get("feedback_score", 0.0),
                )
            logger.info("Loaded %d skills from %s", len(self._skills), path)
        except (json.JSONDecodeError, KeyError) as e:
            logger.error("Failed to load skills: %s", e)
