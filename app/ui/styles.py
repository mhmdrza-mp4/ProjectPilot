"""Reusable styling for the decision UI (no Streamlit calls in this module).

A restrained industrial/project-control aesthetic: compact KPI cards,
status badges with strong color semantics (ready / review / invalid),
info/warning/error message accents, a workflow stepper, clean tables,
and comfortable spacing. Pure CSS + tokens only — the rendering code
consumes these constants; no component logic lives here.
"""

APPLICATION_CSS = """
<style>
    /* ---------- typography & spacing ---------- */
    .block-container { padding-top: 1.6rem; padding-bottom: 3rem; }
    h1 { letter-spacing: 0.2px; margin-bottom: 0.2rem; }
    h2 { display: block; padding-top: 0.55rem; margin-bottom: 0;
         border-top: 1px solid #eef0f2; }
    section[data-testid="stSidebar"] > div { padding-top: 1.2rem; }
    a, p, li { font-size: 0.92rem; }

    /* ---------- header identity ---------- */
    .product-title {
        font-size: 2rem; font-weight: 700; color: #1e293b; line-height: 1.15;
    }
    .product-subtitle {
        font-size: 0.95rem; color: #64748b; margin-top: 0.1rem;
    }

    /* ---------- workflow stepper ---------- */
    .stepper { display: flex; gap: 0.4rem; margin: 0.7rem 0 0.3rem 0; flex-wrap: wrap; }
    .step {
        font-size: 0.74rem; font-weight: 600; padding: 0.18rem 0.6rem;
        border-radius: 999px; border: 1px solid #e3e6ea; color: #94a3b8;
        background: #f7f8fa; white-space: nowrap;
    }
    .step.done { color: #166534; background: #f0fdf4; border-color: #bbf7d0; }
    .step.active {
        color: #1e40af; background: #eff6ff; border-color: #93c5fd;
        box-shadow: 0 0 0 2px rgba(59, 130, 246, 0.08);
    }

    /* ---------- KPI cards ---------- */
    .kpi-card {
        background: #f7f8fa; border: 1px solid #e3e6ea;
        border-left: 4px solid #4a5568; border-radius: 8px;
        padding: 0.7rem 1rem 0.6rem 1rem; min-height: 86px;
    }
    .kpi-card.review  { border-left-color: #d97706; }
    .kpi-card.invalid { border-left-color: #dc2626; }
    .kpi-card.ready   { border-left-color: #16a34a; }
    .kpi-label {
        font-size: 0.72rem; font-weight: 600; letter-spacing: 0.08em;
        text-transform: uppercase; color: #64748b; margin-bottom: 0.15rem;
    }
    .kpi-value {
        font-size: 1.55rem; font-weight: 700; color: #1e293b; line-height: 1.1;
    }
    .kpi-value.review  { color: #92400e; }
    .kpi-value.invalid { color: #991b1b; }
    .kpi-value.ready   { color: #166534; }
    .kpi-sub { font-size: 0.74rem; color: #94a3b8; margin-top: 0.15rem; }

    /* ---------- status badges ---------- */
    .status-badge {
        display: inline-block; padding: 0.22rem 0.75rem; border-radius: 999px;
        font-size: 0.8rem; font-weight: 700; letter-spacing: 0.05em;
    }
    .badge.ready   { background: #dcfce7; color: #166534; border: 1px solid #86efac; }
    .badge.review  { background: #fef9c3; color: #854d0e; border: 1px solid #fde047; }
    .badge.invalid { background: #fee2e2; color: #991b1b; border: 1px solid #fca5a5; }
    .badge.info    { background: #eff6ff; color: #1e40af; border: 1px solid #93c5fd; }
    .badge.warning { background: #fff7ed; color: #9a3412; border: 1px solid #fdba74; }
    .badge.neutral { background: #f7f8fa; color: #4a5568; border: 1px solid #e3e6ea; }

    /* ---------- review chips ---------- */
    .review-chip {
        display: inline-block; padding: 0.12rem 0.55rem; margin: 0.1rem 0.3rem 0.1rem 0;
        border-radius: 999px; font-size: 0.72rem; font-weight: 600;
        background: #fff7ed; color: #9a3412; border: 1px solid #fed7aa;
    }

    /* ---------- read-only note ---------- */
    .readonly-note {
        font-size: 0.76rem; color: #57606a;
        border-left: 3px solid #a5b4fc; padding: 0.25rem 0 0.25rem 0.6rem;
        margin-bottom: 0.4rem;
    }

    /* ---------- empty states ---------- */
    .empty-state {
        border: 1px dashed #d1d5db; border-radius: 10px; padding: 1rem 1.2rem;
        color: #64748b; font-size: 0.9rem; background: #fbfcfd;
    }
    .empty-state .empty-title {
        font-weight: 700; color: #4a5568; font-size: 0.95rem; margin-bottom: 0.2rem;
    }

    /* ---------- data tables (Streamlit dataframes) ---------- */
    div[data-testid="stDataFrame"] { border: 1px solid #eef0f2; border-radius: 8px; }
</style>
"""

# ReviewStatus.value -> badge CSS class (existing review semantics only).
STATUS_CSS_CLASS = {
    "ready": "ready",
    "review_required": "review",
    "invalid": "invalid",
}

# Presentation-only message levels for consistent info/warning/error styling.
MESSAGE_BADGE = {"info": "info", "warning": "warning", "neutral": "neutral"}
