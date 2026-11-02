"""FixFlow AI Streamlit SaaS UI. Works with existing FixFlow FastAPI routes."""
from __future__ import annotations
import html
import base64
import json
import os
import re
import requests
from datetime import datetime
from pathlib import Path
import streamlit as st
from frontend.api_client import call, BASE

st.set_page_config(page_title="FixFlow AI | NovaCart Support", page_icon="🛒", layout="wide", initial_sidebar_state="expanded")
logout_warning = st.session_state.pop("logout_warning", None)
if logout_warning:
    st.warning(logout_warning)
CSS = Path(__file__).with_name("style.css")
st.markdown(f"<style>{CSS.read_text(encoding='utf-8')}</style>", unsafe_allow_html=True)

def apply_local_banner_images():
    """Embed existing local banner PNGs without requiring public URLs."""
    assets = Path(__file__).resolve().parent / "assets"
    rules = []
    for filename, selector in (("login_support.png", ".ff-login-hero"),
                               ("dashboard_banner.png", ".ff-welcome")):
        path = assets / filename
        if path.is_file():
            encoded = base64.b64encode(path.read_bytes()).decode("ascii")
            rules.append(
                f'{selector} {{background-image: url("data:image/png;base64,{encoded}") !important;'
                'background-repeat: no-repeat !important;'
                'background-position: center center !important;'
                'background-size: cover !important;}'
            )
        else:
            st.warning(f"Banner image missing: {path}. Restore it to show the original artwork.")
    if rules:
        st.markdown("<style>" + "\n".join(rules) + "</style>", unsafe_allow_html=True)

apply_local_banner_images()


STATUS = {"QUEUED": ("Queued", "blue"), "PROCESSING": ("AI processing", "blue"), "ANSWERED": ("Answered", "green"), "ESCALATED": ("Human review", "red"), "RESOLVED": ("Resolved", "green"), "AWAITING_CUSTOMER": ("Awaiting customer", "amber")}
POLICY_NAMES = {"shipping.md": "Shipping Policy", "refunds.md": "Refund & Return Policy", "products.md": "Product Information Policy", "account.md": "Account & Login Policy"}

for name, default in {"token": None, "page": "Home", "ticket_id": None, "complaint_step": 1, "draft": {}, "review_tab": "Conversation"}.items():
    if name not in st.session_state:
        st.session_state[name] = default


def safe(value):
    return html.escape(str(value or ""))


def stamp(value):
    try:
        return datetime.fromtimestamp(float(value)).strftime("%d %b %Y, %I:%M %p")
    except (TypeError, ValueError, OSError, OverflowError):
        return "—"


def public_id(item):
    return item.get("public_id") or item.get("ticket_code") or "Support ticket"


def pill(status):
    label, tone = STATUS.get(status, (status or "Unknown", "blue"))
    return f'<span class="ff-pill ff-{tone}">{safe(label)}</span>'


def api(method, path, **kwargs):
    try:
        return call(method, path, st.session_state.token, **kwargs)
    except Exception:
        st.error(
            "We couldn't complete your request. "
            "The service may be temporarily unavailable. "
            "Please try again shortly."
        )
        return None


def go(page, ticket=None):
    st.session_state.page = page
    st.session_state.ticket_id = ticket
    st.rerun()


def title(name, description=None):
    st.markdown(f'<div class="ff-page-title">{safe(name)}</div>', unsafe_allow_html=True)
    if description:
        st.markdown(f'<p class="ff-subtitle">{safe(description)}</p>', unsafe_allow_html=True)


def metric_card(label, value, icon, tone="blue"):
    st.markdown(f'<div class="ff-metric"><div class="ff-metric-icon ff-{tone}">{icon}</div><div><div class="ff-metric-label">{safe(label)}</div><div class="ff-metric-number">{safe(value)}</div></div></div>', unsafe_allow_html=True)


def ticket_list(items, admin=False):
    if not items:
        st.info("No tickets to display.")
        return
    for t in items:
        a, b, c = st.columns([5, 2, 1.4], vertical_alignment="center")
        with a:
            st.markdown(f"**{safe(t.get('subject', 'Support request'))}**")
            sub = f"{public_id(t)} · {stamp(t.get('created_at'))}"
            if admin:
                sub += f" · ₹{float(t.get('claimed_amount') or 0):,.2f}"
            st.caption(sub)
        with b:
            st.markdown(pill(t.get("status")), unsafe_allow_html=True)
        with c:
            if st.button("View →", key=f"open_{t['id']}", use_container_width=True):
                go("Ticket", t["id"])
        st.divider()


def source_names(citations):
    try:
        ids = json.loads(citations) if isinstance(citations, str) else citations
        return ", ".join(safe(str(x)) for x in ids) if ids else "No verified citations"
    except (TypeError, ValueError):
        return "Citation data unavailable"


def registration_errors(full_name, phone, email, password, confirm_password):
    errors = []
    name = ' '.join(full_name.split())
    if not 2 <= len(name) <= 100 or not re.fullmatch(r"[^\W\d_]+(?:[\s.'’-][^\W\d_]+)*", name, re.UNICODE):
        errors.append('Full name: enter 2–100 valid characters (letters, spaces and name punctuation).')
    normalized_email = email.strip().lower()
    if not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+", normalized_email) or '..' in normalized_email or len(normalized_email) > 254:
        errors.append('Email: enter a valid email address.')
    cleaned_phone = re.sub(r'[\s-]', '', phone)
    if cleaned_phone and not re.fullmatch(r'(?:\+91)?[6-9]\d{9}', cleaned_phone):
        errors.append('Phone: enter a valid 10-digit Indian mobile number, optionally prefixed with +91.')
    if not 12 <= len(password) <= 128 or not all((re.search(r'[A-Z]', password), re.search(r'[a-z]', password), re.search(r'\d', password), re.search(r'[^A-Za-z0-9\s]', password))):
        errors.append('Password: use 12–128 characters including uppercase, lowercase, number and special character.')
    if password != confirm_password:
        errors.append('Confirm password: passwords do not match.')
    return errors


def login_screen():
    left, right = st.columns([1.05, 1], gap="large")
    with left:
        st.markdown('''<div class="ff-login-hero"><div class="ff-login-logo">🛒 &nbsp;FixFlow AI</div><div class="ff-login-small">NovaCart Customer Support</div><h1>Smart support.<br>Real solutions.<br>Happier shopping.</h1><p>✓ AI-powered support</p><p>✓ Fast, reliable responses</p><p>✓ Transparent ticket tracking</p><p>✓ Human review for complex cases</p></div>''', unsafe_allow_html=True)
    with right:
        st.markdown('<div class="ff-login-heading">Welcome to FixFlow AI</div><p class="ff-subtitle">Sign in or create a NovaCart support account.</p>', unsafe_allow_html=True)
        login, register, verify = st.tabs(["Login", "Register", "Verify / Reset"])
        with login:
            with st.form("login_form"):
                email = st.text_input("Email", key="login_email", placeholder="you@example.com")
                password = st.text_input("Password", type="password", key="login_password")
                submitted = st.form_submit_button("Login", type="primary", use_container_width=True)
            if submitted:
                try:
                    result = call("POST", "/auth/login", json={"email": email.strip(), "password": password})
                    st.session_state.token = result["access_token"]
                    st.session_state.page = "Home"
                    st.rerun()
                except Exception as exc:
                    st.error(str(exc))
        with register:
            with st.form("register_form"):
                full_name = st.text_input("Full name *", key="register_name", max_chars=100)
                phone = st.text_input("Mobile number (optional)", key="register_phone", placeholder="9876543210 or +919876543210")
                email = st.text_input("Email *", key="register_email", max_chars=254)
                password = st.text_input("Password *", type="password", key="register_password", help="12–128 characters with uppercase, lowercase, number and special character")
                confirm_password = st.text_input("Confirm password *", type="password", key="register_confirm_password")
                submitted = st.form_submit_button("Create account", type="primary", use_container_width=True)
            if submitted:
                errors = registration_errors(full_name, phone, email, password, confirm_password)
                if errors:
                    for error in errors:
                        st.error(error)
                else:
                    try:
                        result = call("POST", "/auth/register", json={
                            "email": email.strip().lower(), "password": password,
                            "full_name": ' '.join(full_name.split()),
                            "phone": phone.strip() or None,
                        })
                        st.success(result.get("message", "Account created. Please verify your email."))
                    except Exception as exc:
                        st.error(str(exc))
        with verify:
            st.caption("Verification and password-reset codes are sent to your registered email address.")
            token = st.text_input("Verification token", key="verify_token")
            if st.button("Verify email", use_container_width=True):
                try:
                    call("POST", "/auth/verify", json={"token": token.strip()})
                    st.success("Email verified. You can now log in.")
                except Exception as exc:
                    st.error(str(exc))
            st.divider()
            reset_email = st.text_input("Password reset email", key="reset_email")
            if st.button("Request reset token"):
                try:
                    result = call("POST", "/auth/password-reset/request", json={"email": reset_email.strip()})
                    st.info(result["message"])
                except Exception as exc:
                    st.error(str(exc))
            reset_token = st.text_input("Reset token", key="reset_token")
            new_password = st.text_input("New password (12+ characters)", type="password", key="reset_password")
            if st.button("Set new password"):
                try:
                    call("POST", "/auth/password-reset/confirm", json={"token": reset_token.strip(), "password": new_password})
                    st.success("Password updated. Sign in again.")
                except Exception as exc:
                    st.error(str(exc))


def sidebar(user, profile=None):
    admin = user.get("role") == "admin"
    with st.sidebar:
        st.markdown('<div class="ff-sidebar-logo">🛒 FixFlow AI</div><div class="ff-sidebar-caption">NovaCart Support</div>', unsafe_allow_html=True)
        st.divider()
        pages = (["Dashboard", "Tickets", "Escalations", "Refund reviews", "Audit logs"] if admin else ["Home", "My tickets", "New complaint", "Help center", "Account"])
        for page in pages:
            icons = {"Home": "⌂", "My tickets": "▤", "New complaint": "＋", "Help center": "ⓘ", "Account": "⚙", "Dashboard": "▦", "Tickets": "▤", "Escalations": "⚑", "Refund reviews": "₹", "Audit logs": "◷"}
            if st.button(f"{icons.get(page, '•')}   {page}", key=f"nav_{page}", use_container_width=True, type="primary" if st.session_state.page == page else "secondary"):
                go(page)
        st.divider()
        if admin:
            st.caption("Administrator")
        else:
            display_name = (profile or {}).get("full_name") or user.get("full_name") or "Customer"
            customer_id = user.get("customer_code") or user.get("public_id")
            st.markdown(f"**{safe(display_name)}**")
            st.caption(f"Customer · {safe(customer_id)}" if customer_id else "Customer")
        if st.button("Log out", use_container_width=True):
            logout_failed = False
            try:
                call("POST", "/auth/logout", st.session_state.token)
            except Exception:
                logout_failed = True
            st.session_state.token = None
            st.session_state.ticket_id = None
            if logout_failed:
                st.session_state["logout_warning"] = (
                    "You have been signed out of this browser, but the server "
                    "could not confirm session revocation. Your previous session "
                    "may remain active until it expires."
                )
            st.rerun()
    return admin


def customer_home(rows, user=None):
    st.markdown(f'''<div class="ff-welcome"><div><div class="ff-welcome-heading">Welcome, {safe((user or {}).get("full_name") or "NovaCart customer")}!</div><p>We're here to help with any issue related to your orders, account, or products.</p></div></div>''', unsafe_allow_html=True)
    a, b, c, d = st.columns(4)
    with a: metric_card("Total tickets", len(rows), "▤")
    with b: metric_card("Processing", sum(x["status"] in ("QUEUED", "PROCESSING") for x in rows), "◷")
    with c: metric_card("Answered", sum(x["status"] in ("ANSWERED", "RESOLVED") for x in rows), "✓", "green")
    with d: metric_card("Human review", sum(x["status"] == "ESCALATED" for x in rows), "⚑", "amber")
    st.write("")
    a, b, c = st.columns(3)
    with a:
        if st.button("＋  New complaint", use_container_width=True, type="primary"):
            go("New complaint")
    with b:
        if st.button("▤  My tickets", use_container_width=True):
            go("My tickets")
    with c:
        if st.button("ⓘ  Help center", use_container_width=True):
            go("Help center")
    st.write("")
    title("Recent tickets")
    ticket_list(rows[:5])


def complaint():
    title("Raise a new complaint", "Please provide the details so we can assist you better.")
    st.markdown('<div class="ff-steps">① Complaint details &nbsp; → &nbsp; ② Review & submit &nbsp; → &nbsp; ③ Confirmation</div>', unsafe_allow_html=True)
    if st.session_state.complaint_step == 1:
        step_col, form_col = st.columns([1, 3], gap="large")
        with step_col:
            st.markdown("### Your progress")
            st.markdown("🔵 **1. Complaint details**")
            st.markdown("⚪ 2. Review & submit")
            st.markdown("⚪ 3. Confirmation")
        with form_col:
          orders = api("GET", "/account/orders") or []
          options = ["No related order"] + [f"{o['order_code']} · {o['status']} · ₹{o['total_amount']:,.2f}" for o in orders]
          with st.form("complaint_details"):
              category = st.selectbox("Category", ["Shipping & delivery", "Returns & refunds", "Product & warranty", "Account & login", "Payments & billing", "Other"])
              subject = st.text_input("Subject", value=st.session_state.draft.get("subject", ""), max_chars=150)
              description = st.text_area("Description", value=st.session_state.draft.get("description", ""), height=170, max_chars=5000)
              amount = st.number_input("Claimed amount (₹, optional)", min_value=0.0, step=1.0, value=float(st.session_state.draft.get("amount", 0)))
              related_order = st.selectbox("Related order (optional)", options)
              st.caption("Please don't include passwords, payment card details, or other secrets. You can attach supporting photos after submitting the complaint.")
              next_step = st.form_submit_button("Next: review complaint →", type="primary")
        if next_step:
            if len(subject.strip()) < 3 or len(description.strip()) < 10:
                st.warning("Subject must be at least 3 characters; description at least 10.")
            else:
                st.session_state.draft = {"category": category, "subject": subject.strip(), "description": description.strip(), "amount": amount, "related_order": related_order.split(" · ")[0] if related_order != "No related order" else None}
                st.session_state.complaint_step = 2
                st.rerun()
    elif st.session_state.complaint_step == 2:
        d = st.session_state.draft
        st.markdown(f'<div class="ff-panel"><b>{safe(d["subject"])}</b><p class="ff-muted">{safe(d["category"])} · Claimed amount: {"₹" + format(d["amount"], ",.2f") if d["amount"] else "Not specified"}</p><p>{safe(d["description"])}</p></div>', unsafe_allow_html=True)
        left, right = st.columns(2)
        with left:
            if st.button("← Edit details", use_container_width=True):
                st.session_state.complaint_step = 1
                st.rerun()
        with right:
            if st.button("Submit complaint", type="primary", use_container_width=True):
                result = api("POST", "/tickets", json={"subject": d["subject"], "description": d["description"], "claimed_amount": d["amount"] or None, "order_code": d.get("related_order")})
                if result:
                    st.session_state.complaint_step = 3
                    st.session_state.ticket_id = result.get("id")
                    st.rerun()
    else:
        st.success("Your complaint was submitted successfully.")
        st.caption("Track the status from My tickets. Processing happens in the background.")
        st.caption("Optional supporting evidence: JPEG, PNG or PDF; maximum 2 MB each, up to 5 files per ticket.")
        evidence_files = st.file_uploader("Attach evidence", type=["jpg", "jpeg", "png", "pdf"], accept_multiple_files=True)
        if st.button("Upload evidence"):
            if not evidence_files:
                st.warning("Select a file first.")
            elif len(evidence_files) > 5:
                st.error("Maximum five files per ticket.")
            else:
                for uploaded in evidence_files:
                    content = uploaded.getvalue()
                    if len(content) > 2 * 1024 * 1024:
                        st.error(f"{uploaded.name}: exceeds 2 MB")
                        continue
                    media = {"image/jpeg": "image/jpeg", "image/png": "image/png", "application/pdf": "application/pdf"}.get(uploaded.type)
                    if not media:
                        st.error(f"{uploaded.name}: unsupported file type")
                        continue
                    response = api("POST", f"/tickets/{st.session_state.ticket_id}/evidence", json={"filename": uploaded.name, "media_type": media, "content_base64": base64.b64encode(content).decode("ascii")})
                    if response:
                        st.success(f"Uploaded {uploaded.name}")
        if st.button("View ticket →", type="primary"):
            go("Ticket", st.session_state.ticket_id)
        if st.button("Submit another complaint"):
            st.session_state.complaint_step = 1
            st.session_state.draft = {}
            go("New complaint")


def processing_status(data):
    status = data.get("status")
    title(public_id(data), f"{data.get('subject', '')} · Submitted {stamp(data.get('created_at'))}")
    st.markdown(pill(status), unsafe_allow_html=True)
    st.write("")
    if status in ("QUEUED", "PROCESSING"):
        stage = 1 if status == "QUEUED" else 2
        st.progress(stage / 3, text="Received → Queued → AI processing → Response")
        st.info("Your request is being processed. You can leave this page and return later.")
        if st.button("Refresh status", type="primary"):
            st.rerun()
    elif status == "ESCALATED":
        st.warning("This request requires human review. No refund or payment has been executed.")
    elif status in ("ANSWERED", "RESOLVED"):
        st.success("A response is available below.")


def conversation(data):
    st.subheader("Conversation")
    messages = data.get("messages", [])
    if not messages:
        st.info("Messages will appear here when available.")
    for m in messages:
        role = m.get("role", "assistant")
        label = {"customer": "You", "assistant": "FixFlow AI", "admin": "NovaCart Support"}.get(role, role.title())
        with st.chat_message("user" if role == "customer" else "assistant", avatar="👤" if role == "customer" else "🛒"):
            st.caption(f"{label} · {stamp(m.get('created_at'))}")
            st.write(m.get("content", ""))
            if role == "assistant" and m.get("citations"):
                with st.expander("Verified policy references", expanded=False):
                    for src in m["citations"]:
                        st.caption(f"{src.get('title', 'Policy document')} — PDF page {src.get('page', '?')} (chunk {src.get('chunk', '?')})")



def evidence_section(ticket_id: str, *, admin: bool = False, resolved: bool = False):
    """List existing evidence for authorized users and allow customer uploads."""
    st.subheader("Supporting evidence")
    evidence = api("GET", f"/tickets/{ticket_id}/evidence")
    if evidence is None:
        st.warning("Could not load evidence. Check the API error above.")
        return
    if not evidence:
        st.info("No evidence files are attached to this ticket yet.")
    for item in evidence:
        eid = item["id"]
        st.write(f"📎 {item['original_name']} · {item['byte_size']:,} bytes")
        preview_key = f"ticket_preview_{ticket_id}_{eid}"
        if st.button("View attachment", key=f"ticket_view_{ticket_id}_{eid}"):
            try:
                response = requests.get(
                    BASE + f"/tickets/{ticket_id}/evidence/{eid}",
                    headers={"Authorization": f"Bearer {st.session_state.token}"},
                    timeout=20,
                )
                response.raise_for_status()
                st.session_state[preview_key] = response.content
            except requests.RequestException:
                st.error("Attachment could not be retrieved.")
        content = st.session_state.get(preview_key)
        if content:
            if item["media_type"] in ("image/jpeg", "image/png"):
                st.image(content, width=350)
            elif item["media_type"] == "application/pdf":
                st.download_button("Download PDF", content,
                    file_name=item["original_name"], mime="application/pdf",
                    key=f"ticket_pdf_{ticket_id}_{eid}")
    if admin or resolved:
        if resolved and not admin:
            st.caption("This ticket is resolved. Existing attachments remain available; new uploads are disabled.")
        return
    st.caption("Attach JPEG, PNG or PDF files (maximum 2 MB each; five files per ticket).")
    uploads = st.file_uploader("Add supporting evidence", type=["jpg", "jpeg", "png", "pdf"],
                               accept_multiple_files=True, key=f"ticket_upload_{ticket_id}")
    if st.button("Upload selected files", key=f"ticket_upload_button_{ticket_id}"):
        if not uploads:
            st.warning("Select a file first.")
            return
        if len(uploads) + len(evidence) > 5:
            st.error("Maximum five evidence files per ticket.")
            return
        success = 0
        for uploaded in uploads:
            content = uploaded.getvalue()
            media = {"image/jpeg", "image/png", "application/pdf"}
            if not content or len(content) > 2 * 1024 * 1024 or uploaded.type not in media:
                st.error(f"{uploaded.name}: unsupported or exceeds 2 MB")
                continue
            result = api("POST", f"/tickets/{ticket_id}/evidence", json={
                "filename": uploaded.name,
                "media_type": uploaded.type,
                "content_base64": base64.b64encode(content).decode("ascii"),
            })
            if result is not None:
                success += 1
        if success:
            st.success(f"Uploaded {success} file(s).")
            st.rerun()


def ticket_detail(admin=False):
    tid = st.session_state.ticket_id
    if not tid:
        go("Tickets" if admin else "My tickets")
    data = api("GET", f"/tickets/{tid}")
    if not data:
        return
    if st.button("← Back to tickets"):
        go("Tickets" if admin else "My tickets")
    processing_status(data)
    refund_decision_summary(tid)
    evidence_section(tid, admin=admin, resolved=data.get("status") == "RESOLVED")
    if admin:
        tabs = st.tabs(["Conversation", "AI analysis", "Policy evidence", "Audit history"])
        with tabs[0]: conversation(data)
        with tabs[1]:
            for d in data.get("drafts", []):
                st.markdown("**Suggested AI response**")
                st.write(d.get("draft", ""))
                st.caption("Requires human review: " + ("Yes" if d.get("requires_review") else "No"))
        with tabs[2]:
            st.info("Verified policy sources and PDF page references")
            try:
                meta = call("GET", f"/tickets/{tid}/citations", token=st.session_state.token).get("citations", [])
                for item in meta:
                    st.write(f"{item['title']} — {item['department']} — PDF page {item['page']} (chunk {item['chunk']})")
                if not meta:
                    st.caption("No verified policy citations for the latest AI result")
            except Exception:
                st.caption("Source details temporarily unavailable")
        with tabs[3]:
            st.markdown("**Observed AutoGen agent activity**")
            events = api("GET", f"/admin/rubric/agent-events/{tid}")
            if events is not None:
                if events:
                    st.caption(
                        "Each row confirms an observed AutoGen speaker, not successful "
                        "completion of that agent's task. Times show when activity "
                        "was recorded, not when individual agents executed."
                    )
                    role_descriptions = {
                        "QueryAgent": "Query analysis / routing role",
                        "KnowledgeAgent": "Knowledge retrieval role",
                        "ResolutionAgent": "Response preparation role",
                        "LogAgent": "Logging / completion role",
                    }
                    activity_rows = []
                    last_job = None
                    step = 0
                    for event in events:
                        job_id = event.get("job_id")
                        if job_id != last_job:
                            step = 0
                            last_job = job_id
                        step += 1
                        activity_rows.append({
                            "Step": step,
                            "Agent": event.get("agent", "Unknown"),
                            "Role": role_descriptions.get(event.get("agent"), "Agent"),
                            "Observation": (
                                "Message observed" if event.get("event") == "MESSAGE_OBSERVED"
                                else event.get("event", "Unknown")
                            ),
                            "Recorded": stamp(event.get("created_at")),
                            "Job": str(job_id)[:8] if job_id else "—",
                        })
                    st.dataframe(activity_rows, use_container_width=True, hide_index=True)
                    st.caption(
                        "Steps are numbered per processing job. Older jobs may have "
                        "their agents listed in configured order rather than observed order. "
                        "Refer to the ticket audit history for workflow outcomes."
                    )
                else:
                    st.info("No recorded agent activity for this ticket. Historical jobs are not backfilled.")
            st.markdown("**Agent execution outputs (admin-only)**")
            st.caption("Agent-authored text captured after processing. Not internal reasoning or tool payloads. "
                       "Only new jobs processed after this update have output records; refresh to see results.")
            outputs = api("GET", f"/admin/rubric/agent-outputs/{tid}")
            if outputs:
                for item in outputs:
                    with st.expander(f"{item.get('agent', 'Agent')} · Job {str(item.get('job_id', ''))[:8]}"):
                        st.text(item.get('output') or '(No text output)')
            elif outputs is not None:
                st.info("No captured agent outputs for this ticket. Historical runs are not backfilled.")
            if st.button("Load audit history"):
                events = api("GET", f"/admin/audit/{tid}")
                if events is not None:
                    for e in events:
                        st.write(f"{stamp(e.get('created_at'))} · {e.get('event')}")
        if data.get("status") == "ESCALATED":
            st.subheader("Edit and approve response")
            drafts = data.get("drafts", [])
            initial = drafts[-1].get("draft", "") if drafts else ""
            with st.form(f"admin_review_{tid}"):
                response = st.text_area("Reviewed response to customer", value=initial, height=200)
                confirm = st.checkbox("I reviewed this response. Publishing sends a message only; it does not issue a refund.")
                publish = st.form_submit_button("Approve and send", type="primary")
            if publish:
                if not confirm or len(response.strip()) < 2:
                    st.warning("Enter a reviewed response and confirm before publishing.")
                else:
                    result = api("POST", f"/admin/tickets/{tid}/publish", json={"response": response.strip(), "version": data["version"]})
                    if result:
                        st.success("Response published.")
                        st.rerun()
    else:
        conversation(data)
        if data.get("status") == "ANSWERED":
            st.subheader("Is your support case resolved?")
            st.caption("You can confirm resolution or keep the ticket open and request more help. Resolving the ticket does not mean any refund payment was made.")
            with st.form(f"customer_resolve_{tid}"):
                resolution_reason = st.text_area("Resolution confirmation", value="My support question has been addressed.", height=80)
                confirmed = st.checkbox("I want to close this support ticket. I understand this does not confirm payment.")
                resolve_submitted = st.form_submit_button("Mark ticket resolved", type="primary")
            if resolve_submitted:
                if not confirmed or len(resolution_reason.strip()) < 10:
                    st.warning("Confirm and provide at least 10 characters.")
                else:
                    result = api("POST", f"/tickets/{tid}/resolve", json={"reason": resolution_reason.strip(), "version": data["version"]})
                    if result:
                        st.success("Ticket resolved. No payment executed.")
                        st.rerun()
        if data.get("status") != "RESOLVED":
            with st.form(f"follow_up_{tid}"):
                message = st.text_area("Send a follow-up message", placeholder="Type your message here...", height=100)
                submitted = st.form_submit_button("Send follow-up", type="primary")
            if submitted:
                if len(message.strip()) < 2:
                    st.warning("Please enter at least 2 characters.")
                else:
                    result = api("POST", f"/tickets/{tid}/reply", json={"content": message.strip()})
                    if result:
                        st.success("Follow-up submitted. Check the updated ticket status.")
                        st.rerun()
        st.caption("Ordinary follow-ups are queued for AI processing; sensitive requests may require human review.")



def refund_decision_summary(ticket_id: str):
    """Show the recorded decision separately from the support ticket status."""
    review = api("GET", f"/tickets/{ticket_id}/refund-review")
    if not review or not review.get("has_review"):
        return
    st.subheader("Refund review status")
    status = review.get("status")
    if status == "PENDING":
        st.info("Refund review pending a human decision. No payment has been executed.")
    elif status == "APPROVED":
        st.success("Refund review approved for further processing. No refund or payment has been executed.")
    elif status == "REJECTED":
        st.warning("Refund review rejected. No refund or payment has been executed.")
    if review.get("decided_at"):
        st.caption(f"Decision recorded: {stamp(review['decided_at'])}")
    st.caption(f"Payment execution: {review.get('execution_status', 'NOT_CONFIGURED')}. Ticket status is managed separately.")


def refund_reviews_page():
    """Admin review UI; all authorization and approval rules live in FastAPI."""
    title("Refund reviews", "Inspect evidence and record a human decision")
    st.warning("Review decisions do not execute refunds or payments.")
    reviews = api("GET", "/admin/refund-reviews")
    if reviews is None:
        return
    if not reviews:
        st.info("No pending refund reviews.")
    st.markdown("### Pending decisions")
    for review in reviews:
        tid = review.get("ticket_id")
        if not tid:
            continue
        with st.expander(f"Ticket {safe(tid)} · ₹{safe(review.get('amount', '—'))}"):
            st.caption("Review the actual files. An upload is not proof of a valid claim.")
            evidence = api("GET", f"/tickets/{tid}/evidence")
            if evidence is None:
                st.error("Evidence list unavailable; do not approve this request.")
                continue
            if not evidence:
                st.info("No evidence uploaded for this ticket. A damaged/defective claim cannot be accepted without inspected evidence.")
            selected_ids = []
            for item in evidence:
                eid = item["id"]
                st.write(f"{safe(item['original_name'])} · {safe(item['media_type'])} · {item['byte_size']} bytes")
                if st.button("Inspect evidence", key=f"inspect_{tid}_{eid}"):
                    try:
                        response = requests.get(BASE + f"/tickets/{tid}/evidence/{eid}",
                            headers={"Authorization": f"Bearer {st.session_state.token}"}, timeout=20)
                        response.raise_for_status()
                        st.session_state[f"preview_{tid}_{eid}"] = response.content
                    except requests.RequestException:
                        st.error("Evidence could not be retrieved.")
                content = st.session_state.get(f"preview_{tid}_{eid}")
                if content:
                    if item["media_type"] in ("image/jpeg", "image/png"):
                        st.image(content, width=350)
                    else:
                        st.download_button("Download PDF for inspection", content,
                            file_name="evidence.pdf", mime="application/pdf", key=f"pdf_{tid}_{eid}")
                if st.checkbox("Include inspected evidence", key=f"accept_{tid}_{eid}",
                               disabled=not bool(content)) and content:
                    selected_ids.append(eid)
            st.markdown("**Step 1: Human evidence assessment**")
            claim_type = st.selectbox("Claim type", ["DAMAGED_DEFECTIVE", "OTHER"], key=f"claim_{tid}")
            statuses = ["ACCEPTED", "REJECTED"] if claim_type == "DAMAGED_DEFECTIVE" else ["NOT_REQUIRED"]
            with st.form(f"assessment_{tid}"):
                status = st.selectbox("Assessment", statuses)
                assessment_reason = st.text_area("Assessment reason", max_chars=2000)
                assess_clicked = st.form_submit_button("Save assessment")
            if assess_clicked:
                if len(assessment_reason.strip()) < 5:
                    st.warning("Assessment reason must contain at least five characters.")
                elif claim_type == "DAMAGED_DEFECTIVE" and status == "ACCEPTED" and not selected_ids:
                    st.warning("Select at least one inspected evidence file.")
                else:
                    result = api("POST", f"/admin/refund-reviews/{tid}/assessment", json={
                        "claim_type": claim_type, "evidence_status": status,
                        "evidence_ids": selected_ids if claim_type == "DAMAGED_DEFECTIVE" else [],
                        "reason": assessment_reason.strip(), "version": review["version"]})
                    if result is not None:
                        st.success("Assessment saved. Backend approval checks remain mandatory.")
            st.markdown("**Step 2: Review decision**")
            with st.form(f"refund_decision_{tid}"):
                decision = st.radio("Decision", ["REJECTED", "APPROVED"], horizontal=True)
                reason = st.text_area("Decision reason", max_chars=2000)
                confirmed = st.checkbox("I understand that no refund or payment will be executed.")
                submitted = st.form_submit_button("Record decision", type="primary")
            if submitted:
                if not confirmed or len(reason.strip()) < 5:
                    st.warning("Confirm non-execution and provide a reason of at least five characters.")
                else:
                    result = api("POST", f"/admin/refund-reviews/{tid}/decision", json={
                        "decision": decision, "reason": reason.strip(), "version": review["version"]})
                    if result is not None:
                        st.success("Decision recorded. No payment executed.")
                        st.rerun()


    st.divider()
    st.markdown("### Completed refund reviews")
    history = api("GET", "/admin/refund-reviews/history")
    if history is not None:
        if not history:
            st.info("No completed refund reviews yet.")
        for item in history:
            with st.expander(f"{safe(item.get('ticket_code') or item['ticket_id'])} · {safe(item['status'])} · ₹{safe(item['amount'])}"):
                st.write(f"Decision: {item['status']}")
                st.write(f"Recorded: {stamp(item.get('decided_at'))}")
                st.write(f"Reason: {item.get('reason') or '—'}")
                st.caption(f"Execution status: {item['execution_status']} — no payment executed")


def admin_dashboard(queue, tickets, complaint_rows):
    """Overview of the authenticated admin ticket listing, plus review queue."""
    from collections import Counter

    title("Admin dashboard", "NovaCart support operations · Human approval workspace")
    statuses = Counter(str(t.get("status") or "UNKNOWN").upper() for t in tickets)
    answered = statuses["ANSWERED"]
    resolved = statuses["RESOLVED"]
    escalated = statuses["ESCALATED"]
    pending = sum(v for k, v in statuses.items() if k in {"OPEN", "PENDING", "QUEUED", "PROCESSING"})
    first = st.columns(5)
    for col, label, value, symbol, tone in zip(
        first,
        ("Total tickets", "AI answered", "Escalated", "Resolved", "Pending"),
        (len(tickets), answered, escalated, resolved, pending),
        ("▤", "✦", "⚑", "✓", "◷"),
        ("blue", "blue", "red", "blue", "amber"),
    ):
        with col:
            metric_card(label, value, symbol, tone)

    st.markdown("#### Ticket status overview")
    if tickets:
        status_rows = [{"Status": status, "Tickets": count}
                       for status, count in sorted(statuses.items())]
        st.dataframe(status_rows, use_container_width=True, hide_index=True)
        st.markdown("#### Tickets by category")
        # The authenticated complaint-log endpoint supplies the same category
        # displayed in the rubric CSV. Match by public ticket ID, never by subject.
        classifications = {
            str(row.get("ticket_id") or "").strip(): (
                str(row.get("category · urgency · sentiment") or "")
                .split(" · ", 1)[0]
                .strip()
            )
            for row in complaint_rows
        }
        categories = Counter(
            classifications.get(public_id(t))
            or "Not classified"
            for t in tickets
        )
        st.dataframe(
            [{"Category": name, "Tickets": count} for name, count in
             sorted(categories.items(), key=lambda pair: (-pair[1], pair[0]))],
            use_container_width=True, hide_index=True,
        )
        st.markdown("#### Recent tickets")
        ticket_list(tickets[:5], admin=True)
    else:
        st.info("No tickets have been created in this database yet.")

    st.markdown("#### Human review workspace")
    a, b, c = st.columns(3)
    with a:
        metric_card("Pending review", len(queue), "⚑", "red")
    with b:
        metric_card("AI drafts", sum(1 for x in queue if x.get("status") == "ESCALATED"), "▤", "amber")
    with c:
        metric_card("High-value requests", sum(float(x.get("claimed_amount") or 0) >= 500 for x in queue), "₹", "blue")
    st.markdown("#### Escalated tickets")
    ticket_list(queue, admin=True)


def help_center_page():
    title("NovaCart Help Center", "Find answers from approved policies without creating a ticket.")
    st.markdown('<div class="ff-help-hero"><h3>How can we help you today?</h3><p>Search shipping, returns, products and account policies. For personal orders and refund requests, our support team can help through a ticket.</p></div>', unsafe_allow_html=True)
    categories = [
        ("🚚 Shipping & Delivery", "What is the standard delivery timeframe?"),
        ("↩️ Returns & Refunds", "What is the return policy?"),
        ("📦 Products & Warranty", "What does the standard product warranty cover?"),
        ("🔐 Account & Security", "what is MFA Reset Authorization Hierarchy ?"),
    ]
    cols = st.columns(2)
    for i, (label, sample) in enumerate(categories):
        with cols[i % 2]:
            if st.button(label, key=f"help_cat_{i}", use_container_width=True):
                st.session_state.help_question = sample
    with st.form("help_search_form"):
        question = st.text_area("Ask a policy question", value=st.session_state.get("help_question", ""), height=100, max_chars=500, placeholder="e.g. What is NovaCart's standard delivery timeframe?")
        submitted = st.form_submit_button("Find an answer", type="primary", use_container_width=True)
    if submitted:
        if len(question.strip()) < 5:
            st.warning("Please enter a question of at least five characters.")
        else:
            with st.spinner("Searching approved NovaCart policies..."):
                result = api("POST", "/help/ask", json={"question": question.strip()})
            if result is not None:
                st.session_state.help_result = result
                st.session_state.help_question = question
    result = st.session_state.get("help_result")
    if result:
        if result.get("status") == "answered":
            st.success("Answer found in approved policy documents")
            st.markdown(result.get("answer", ""))
            st.caption(result.get("note", ""))
            with st.expander("Policy sources", expanded=True):
                for src in result.get("sources", []):
                    document_title = safe(src.get("title") or src.get("document") or "Policy document")
                    version = src.get("version")
                    page = src.get("page")
                    chunk_id = src.get("chunk_id")

                    citation_details = []
                    if version:
                        citation_details.append(safe(str(version)))
                    if page is not None:
                        citation_details.append(f"PDF page {safe(str(page))}")
                    if chunk_id:
                        citation_details.append(f"Chunk {safe(str(chunk_id))}")

                    citation_text = " · ".join(citation_details)
                    st.markdown(
                        f"**{document_title}**"
                        + (f" · {citation_text}" if citation_text else "")
                    )
        else:
            st.info(result.get("answer", "Please create a support ticket for assistance."))
    st.divider()
    st.markdown("#### Need help with your own order or refund?")
    st.caption("Case-specific requests are handled securely through tickets. Refund requests of ₹500 or more require human review.")
    if st.button("Create a support ticket →", type="primary", key="help_ticket"):
        go("New complaint")

def main():
    st.markdown('<div class="ff-dev-notice">Local-only development build · Use fictional data · No public deployment until security and recovery tests pass.</div>', unsafe_allow_html=True)
    if not st.session_state.token:
        login_screen()
        return
    user = api("GET", "/auth/me")
    if user is None:
        st.warning(
            "The backend is unavailable or your session could not be verified. "
            "You can sign out locally and return to the login screen."
        )
        if st.button("Log out and return to login", key="offline_logout"):
            st.session_state.token = None
            st.session_state.ticket_id = None
            st.session_state["logout_warning"] = (
                "You have been signed out of this browser. "
                "Server-side session revocation could not be confirmed. "
                "Your previous session may remain active until it expires."
            )
            st.rerun()
        return
    profile = None
    if user.get("role") != "admin":
        profile = api("GET", "/account/profile") or {}
    admin = sidebar(user, profile)
    page = st.session_state.page
    if page == "Ticket":
        ticket_detail(admin=admin)
        return
    if admin:
        if page in ("Dashboard", "Home", "Escalations"):
            queue = api("GET", "/admin/queue")
            if queue is None:
                return
        if page in ("Dashboard", "Home"):
            all_tickets = api("GET", "/tickets")
            if all_tickets is None:
                return
            complaint_rows = api("GET", "/admin/rubric/complaints")
            if complaint_rows is None:
                st.warning("Ticket classifications are temporarily unavailable.")
                complaint_rows = []
            admin_dashboard(queue, all_tickets, complaint_rows)
        elif page in ("Tickets", "Escalations"):
            # Authenticated /tickets returns all tickets for admins.
            # /admin/queue remains restricted to escalated tickets.
            rows = api("GET", "/tickets") if page == "Tickets" else queue
            if rows is None:
                return
            title("Escalations" if page == "Escalations" else "Support tickets",
                  "Review requests requiring human attention." if page == "Escalations"
                  else "Browse all tickets and inspect agent execution history.")
            term = st.text_input("Search by ticket ID or subject", placeholder="Search tickets")
            term = term.strip().casefold()
            ticket_list([t for t in rows if term in
                         (public_id(t) + " " + (t.get("subject") or "")).casefold()], admin=True)
        elif page == "Refund reviews":
            refund_reviews_page()
        elif page in ("Audit logs"):
            title("Complaint log", "Section 7 complaint log · Admin-only")
            rows = api("GET", "/admin/rubric/complaints")
            if rows is not None:
                st.caption("Unassessed fields are marked explicitly; historical agent steps are not fabricated.")
                st.dataframe(rows, use_container_width=True, hide_index=True)
                if st.button("Prepare complaint CSV", key="prepare_complaint_csv"):
                    try:
                        response = requests.get(BASE + "/admin/rubric/complaints.csv", headers={"Authorization": f"Bearer {st.session_state.token}"}, timeout=30)
                        response.raise_for_status()
                        st.session_state["rubric_csv"] = response.content
                    except requests.RequestException:
                        st.error("CSV could not be retrieved. Check the FastAPI server.")
                if st.session_state.get("rubric_csv"):
                    st.download_button("Download complaint log CSV", st.session_state["rubric_csv"], "fixflow_complaint_log.csv", "text/csv")
            st.caption("For agent activity, open an admin ticket and select Audit history.")
        return
    rows = api("GET", "/tickets")
    if rows is None:
        return
    if page in ("Home", "Dashboard"):
        customer_home(rows, profile or user)
    elif page == "New complaint":
        complaint()
    elif page == "My tickets":
        title("My tickets", "Select a ticket to see progress, responses and follow-ups.")
        term = st.text_input("Search tickets", placeholder="Ticket number or subject")
        ticket_list([t for t in rows if term.lower() in (public_id(t) + " " + t.get("subject", "")).lower()])
    elif page == "Help center":
        help_center_page()
    elif page == "Account":
        title("Account", "Your local NovaCart support profile")
        st.write("Customer ID:", user.get("customer_code") or user.get("public_id") or "—")
        st.write("Role:", user.get("role"))
        st.write("Email verified:", bool(user.get("verified")))
        profile = api("GET", "/account/profile") or {}
        with st.form("profile_edit"):
            name = st.text_input("Full name", value=profile.get("full_name") or "")
            phone = st.text_input("Phone (optional)", value=profile.get("phone") or "")
            save = st.form_submit_button("Save profile", type="primary")
        if save:
            result = api("PUT", "/account/profile", json={"full_name": name, "phone": phone or None})
            if result: st.success("Profile saved")
        st.subheader("My sample orders")
        orders = api("GET", "/account/orders") or []
        if orders:
            for order in orders:
                with st.expander(f"{order['order_code']} · {order['status']} · ₹{order['total_amount']:,.2f}"):
                    for item in order['items']:
                        st.write(f"{item['product_name']} · {item['sku']} · Qty {item['quantity']} · ₹{item['unit_price']:,.2f} each")
        else:
            st.info("No sample orders yet. These are fictional test orders only.")
            if st.button("Create fictional sample orders"):
                result = api("POST", "/account/orders/seed-demo", json={"confirmation": "CREATE_FICTIONAL_ORDERS"})
                if result: st.rerun()


if __name__ == "__main__":
    main()
