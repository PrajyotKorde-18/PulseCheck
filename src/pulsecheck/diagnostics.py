"""Quarantine diagnostics engine computing aggregations, top causes, and concentration flags."""

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from psycopg import Connection

from pulsecheck.config.loader import ConfigBundle

PERCENTAGE_MULTIPLIER = 100.0


@dataclass
class ReasonSummary:
    """Summary of occurrences and share for a single reason code."""

    code: str
    count: int
    share_percentage: float
    description: str = ""


@dataclass
class ConcentrationFlag:
    """Alert flag when a single source or date exceeds the concentration threshold."""

    reason_code: str
    dimension: str  # 'source' or 'date'
    dimension_value: str
    count: int
    total_reason_count: int
    share_percentage: float
    threshold_percentage: float
    message: str


@dataclass
class DiagnosticsReport:
    """Comprehensive diagnostic breakdown of quarantined records."""

    total_quarantined_records: int
    reasons_summary: List[ReasonSummary]
    by_source: Dict[str, int]
    by_scope: Dict[str, int]
    by_date: Dict[str, int]
    concentration_flags: List[ConcentrationFlag]
    group_breakdown: List[Dict[str, Any]] = field(default_factory=list)

    def format_text(self) -> str:
        """Render a formatted human-readable diagnostic report."""
        lines: List[str] = [
            "=" * 60,
            "PulseCheck Quarantine Diagnostics Report",
            "=" * 60,
            f"Total Quarantined Records Analyzed: {self.total_quarantined_records}",
            "",
            "Top Causes of Quarantine:",
            f"{'Reason Code':<28} {'Count':<8} {'Share %':<10}",
            "-" * 60,
        ]

        for s in self.reasons_summary:
            lines.append(f"{s.code:<28} {s.count:<8} {s.share_percentage:>6.1f}%")

        lines.extend(
            [
                "",
                "Breakdown by Source Feed:",
            ]
        )
        for src, cnt in sorted(self.by_source.items(), key=lambda x: x[1], reverse=True):
            pct = (
                (cnt / self.total_quarantined_records * PERCENTAGE_MULTIPLIER)
                if self.total_quarantined_records
                else 0.0
            )
            lines.append(f"  - {src}: {cnt} ({pct:.1f}%)")

        lines.extend(
            [
                "",
                "Breakdown by Scope (City):",
            ]
        )
        for scp, cnt in sorted(self.by_scope.items(), key=lambda x: x[1], reverse=True):
            pct = (
                (cnt / self.total_quarantined_records * PERCENTAGE_MULTIPLIER)
                if self.total_quarantined_records
                else 0.0
            )
            lines.append(f"  - {scp}: {cnt} ({pct:.1f}%)")

        lines.extend(
            [
                "",
                "Breakdown by Logical Date:",
            ]
        )
        for dt, cnt in sorted(self.by_date.items(), key=lambda x: x[0]):
            pct = (
                (cnt / self.total_quarantined_records * PERCENTAGE_MULTIPLIER)
                if self.total_quarantined_records
                else 0.0
            )
            lines.append(f"  - {dt}: {cnt} ({pct:.1f}%)")

        lines.append("")
        if self.concentration_flags:
            lines.append(f"Concentration Alerts ({len(self.concentration_flags)} detected):")
            for f in self.concentration_flags:
                lines.append(f"  [FLAG] {f.message}")
        else:
            lines.append("Concentration Alerts: None detected within configured limits.")

        lines.append("=" * 60)
        return "\n".join(lines)


class QuarantineDiagnostics:
    """Evaluates quarantine records for concentration and top error causes."""

    def __init__(self, config_bundle: ConfigBundle) -> None:
        self.config_bundle = config_bundle
        diag_cfg = config_bundle.checks.diagnostics
        self.max_source_share = diag_cfg.max_source_share
        self.max_day_share = diag_cfg.max_day_share

    def analyze_records(
        self, records: List[Dict[str, Any]]
    ) -> DiagnosticsReport:
        """
        Analyze a list of quarantine records.
        Each record is expected to contain:
        - 'reasons': List[Dict] or JSON string with reason objects
        - 'source': str
        - 'scope': Optional[str]
        - 'logical_date': Optional[str or date]
        - 'raw_row': Optional[Dict]
        """
        total_records = len(records)
        if total_records == 0:
            return DiagnosticsReport(
                total_quarantined_records=0,
                reasons_summary=[],
                by_source={},
                by_scope={},
                by_date={},
                concentration_flags=[],
            )

        reason_counts: Dict[str, int] = {}
        reason_source_counts: Dict[str, Dict[str, int]] = {}
        reason_date_counts: Dict[str, Dict[str, int]] = {}
        source_counts: Dict[str, int] = {}
        scope_counts: Dict[str, int] = {}
        date_counts: Dict[str, int] = {}

        # 4-way group breakdown: (reason, source, scope, date)
        group_counts: Dict[tuple, int] = {}

        for rec in records:
            src = str(rec.get("source") or "unknown")
            source_counts[src] = source_counts.get(src, 0) + 1

            scope_val = str(rec.get("scope") or "global")
            scope_counts[scope_val] = scope_counts.get(scope_val, 0) + 1

            raw_date = rec.get("logical_date")
            if not raw_date and isinstance(rec.get("raw_row"), dict):
                # Fallback to pickup_time date if logical_date missing
                pt = rec["raw_row"].get("pickup_time")
                if pt:
                    raw_date = str(pt)[:10]

            date_str = str(raw_date or "unknown")
            date_counts[date_str] = date_counts.get(date_str, 0) + 1

            # Extract reason codes
            reasons = rec.get("reasons", [])
            if isinstance(reasons, str):
                try:
                    reasons = json.loads(reasons)
                except Exception:
                    reasons = []

            codes: List[str] = []
            if isinstance(reasons, list):
                for r in reasons:
                    if isinstance(r, dict) and "code" in r:
                        codes.append(r["code"])
                    elif isinstance(r, str):
                        codes.append(r)

            if not codes:
                codes = ["UNKNOWN_ERROR"]

            for code in set(codes):
                reason_counts[code] = reason_counts.get(code, 0) + 1

                # Track reason by source
                if code not in reason_source_counts:
                    reason_source_counts[code] = {}
                reason_source_counts[code][src] = (
                    reason_source_counts[code].get(src, 0) + 1
                )

                # Track reason by date
                if code not in reason_date_counts:
                    reason_date_counts[code] = {}
                reason_date_counts[code][date_str] = (
                    reason_date_counts[code].get(date_str, 0) + 1
                )

                # Group key: (code, src, scope_val, date_str)
                grp_key = (code, src, scope_val, date_str)
                group_counts[grp_key] = group_counts.get(grp_key, 0) + 1

        # Build reason summaries sorted by count descending
        reasons_summary: List[ReasonSummary] = []
        for code, cnt in sorted(reason_counts.items(), key=lambda x: x[1], reverse=True):
            share = (cnt / total_records * PERCENTAGE_MULTIPLIER) if total_records else 0.0
            reasons_summary.append(
                ReasonSummary(
                    code=code,
                    count=cnt,
                    share_percentage=round(share, 2),
                )
            )

        # Detect concentrations
        concentration_flags: List[ConcentrationFlag] = []
        for code, total_c in reason_counts.items():
            # Check source concentration
            src_map = reason_source_counts.get(code, {})
            for src_name, src_c in src_map.items():
                src_share = src_c / total_c
                if src_share > self.max_source_share:
                    pct = src_share * PERCENTAGE_MULTIPLIER
                    thresh_pct = self.max_source_share * PERCENTAGE_MULTIPLIER
                    msg = (
                        f"Source '{src_name}' accounts for {pct:.1f}% of {code} "
                        f"({src_c}/{total_c} rows; limit: {thresh_pct:.1f}%)"
                    )
                    concentration_flags.append(
                        ConcentrationFlag(
                            reason_code=code,
                            dimension="source",
                            dimension_value=src_name,
                            count=src_c,
                            total_reason_count=total_c,
                            share_percentage=round(pct, 2),
                            threshold_percentage=thresh_pct,
                            message=msg,
                        )
                    )

            # Check date concentration
            date_map = reason_date_counts.get(code, {})
            for d_val, d_c in date_map.items():
                d_share = d_c / total_c
                if d_share > self.max_day_share:
                    pct = d_share * PERCENTAGE_MULTIPLIER
                    thresh_pct = self.max_day_share * PERCENTAGE_MULTIPLIER
                    msg = (
                        f"Logical date '{d_val}' accounts for {pct:.1f}% of {code} "
                        f"({d_c}/{total_c} rows; limit: {thresh_pct:.1f}%)"
                    )
                    concentration_flags.append(
                        ConcentrationFlag(
                            reason_code=code,
                            dimension="date",
                            dimension_value=d_val,
                            count=d_c,
                            total_reason_count=total_c,
                            share_percentage=round(pct, 2),
                            threshold_percentage=thresh_pct,
                            message=msg,
                        )
                    )

        # Build breakdown records list
        group_breakdown = [
            {
                "reason_code": k[0],
                "source": k[1],
                "scope": k[2],
                "logical_date": k[3],
                "count": v,
            }
            for k, v in sorted(group_counts.items(), key=lambda x: x[1], reverse=True)
        ]

        return DiagnosticsReport(
            total_quarantined_records=total_records,
            reasons_summary=reasons_summary,
            by_source=source_counts,
            by_scope=scope_counts,
            by_date=date_counts,
            concentration_flags=concentration_flags,
            group_breakdown=group_breakdown,
        )

    def analyze_from_db(
        self,
        conn: Connection,
        batch_id: Optional[str] = None,
        source: Optional[str] = None,
        scope: Optional[str] = None,
        status: str = "open",
    ) -> DiagnosticsReport:
        """Query quarantine and batch_registry tables and execute diagnostic analysis."""
        query = """
            SELECT
                q.id,
                q.batch_id,
                q.source,
                q.scope,
                q.raw_row,
                q.reasons,
                b.logical_date
            FROM quarantine q
            LEFT JOIN batch_registry b ON q.batch_id = b.batch_id
            WHERE q.status = %s
        """
        params: List[Any] = [status]

        if batch_id:
            query += " AND q.batch_id = %s"
            params.append(batch_id)
        if source:
            query += " AND q.source = %s"
            params.append(source)
        if scope:
            query += " AND q.scope = %s"
            params.append(scope)

        query += " ORDER BY q.id ASC;"

        with conn.cursor() as cur:
            cur.execute(query, params)
            rows = cur.fetchall()

        records = [
            {
                "id": r[0],
                "batch_id": r[1],
                "source": r[2],
                "scope": r[3],
                "raw_row": r[4],
                "reasons": r[5],
                "logical_date": r[6].isoformat() if isinstance(r[6], (date, datetime)) else r[6],
            }
            for r in rows
        ]
        return self.analyze_records(records)
