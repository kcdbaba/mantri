# Spec: Ambiguity Escalation Router

**Status:** Complete — ready for implementation
**Validated by:** Ashish Part 1 interview, 2026-03-27

---

## Purpose

Given an ambiguity item detected in agent output, decide who to escalate it to
and with what urgency, so that ambiguities are never silently dropped and are
always routed to the right person first time.

---

## Background

From Ashish (verbatim, 2026-03-27):
> "Everything which is above medium or medium and above higher risk rating must
> be directly prompted to me. Otherwise anything low and up to medium might be
> just asked by the senior staff member."
> "Make it faster. Make it faster." (on wait-and-see)
> "Hide ambiguity — that is the main point. It has to be highlighted."

**Escalation targets:**
- `ashish` — business owner, receives medium+ risk, always real-time
- `smita` — senior staff, receives low risk items only
- If Smita is marked unavailable, her items roll up to `ashish`

---

## Inputs

```python
AmbiguityItem:
    id: str                  # unique item identifier
    description: str         # human-readable description of the ambiguity
    risk_level: str          # "low" | "medium" | "high" | "critical"
    order_id: str | None     # associated order, if known
    is_irreversible: bool    # True if acting on a wrong guess causes unrecoverable harm
                             # (e.g. wrong payment, wrong supplier order)
    message_snippet: str     # the raw WhatsApp message(s) that triggered the ambiguity

RoutingContext:
    smita_available: bool    # whether Smita is reachable right now (default: True)
```

---

## Outputs

```python
EscalationDecision:
    target: str              # "ashish" | "smita"
    urgency: str             # "immediate" | "next_digest"
    reason: str              # one-line explanation of why this routing was chosen
    escalate: bool           # always True — ambiguities are never dropped
```

`escalate` is always `True`. This field exists to make the contract explicit:
there is no code path in this function that silently drops an item.

---

## Routing Rules

Apply in order — first matching rule wins:

| # | Condition | Target | Urgency |
|---|---|---|---|
| 1 | `is_irreversible == True` | `ashish` | `immediate` |
| 2 | `risk_level == "critical"` | `ashish` | `immediate` |
| 3 | `risk_level == "high"` | `ashish` | `immediate` |
| 4 | `risk_level == "medium"` | `ashish` | `immediate` |
| 5 | `risk_level == "low"` AND `smita_available == True` | `smita` | `next_digest` |
| 6 | `risk_level == "low"` AND `smita_available == False` | `ashish` | `next_digest` |

**Notes:**
- "medium and above" → Ashish. This matches Ashish's exact words.
- Low items go to Smita for digest (not real-time) — staff prefer batched.
- Irreversible flag overrides risk level: even a "low" risk irreversible action
  goes to Ashish immediately. You cannot undo a wrong payment.
- There is no `urgency == "drop"` or `escalate == False`. Ever.

---

## Edge Cases

1. **Unknown risk level** — if `risk_level` is not one of the four valid values,
   treat as `"high"` and route to Ashish immediately. Fail safe, not fail silent.

2. **Smita unavailable + high risk** — still routes to Ashish immediately (rule 3
   already covers this; the `smita_available` flag only affects rule 5 vs 6).

3. **Irreversible + low risk** — rule 1 fires first → Ashish immediate.
   Example: a low-confidence payment amount match is still irreversible.

4. **Multiple ambiguities for the same order** — each item is routed
   independently. Batching across orders is the caller's responsibility.

---

## Examples

**Example 1 — Payment amount ambiguity (irreversible)**
```
Input:
  risk_level = "low"
  is_irreversible = True
  smita_available = True

Output:
  target = "ashish"
  urgency = "immediate"
  reason = "Irreversible action — overrides risk level, escalates to Ashish immediately"
```

**Example 2 — Unclear order reference (medium risk)**
```
Input:
  risk_level = "medium"
  is_irreversible = False
  smita_available = True

Output:
  target = "ashish"
  urgency = "immediate"
  reason = "Medium risk — routes to Ashish immediately per escalation policy"
```

**Example 3 — Ambiguous staff assignment (low risk, Smita available)**
```
Input:
  risk_level = "low"
  is_irreversible = False
  smita_available = True

Output:
  target = "smita"
  urgency = "next_digest"
  reason = "Low risk — routes to senior staff (Smita) for next digest"
```

**Example 4 — Low risk, Smita unavailable**
```
Input:
  risk_level = "low"
  is_irreversible = False
  smita_available = False

Output:
  target = "ashish"
  urgency = "next_digest"
  reason = "Low risk — Smita unavailable, falls back to Ashish for next digest"
```

**Example 5 — Unknown risk level (fail safe)**
```
Input:
  risk_level = "unknown"
  is_irreversible = False
  smita_available = True

Output:
  target = "ashish"
  urgency = "immediate"
  reason = "Unrecognised risk level 'unknown' — treating as high, escalating to Ashish immediately"
```
