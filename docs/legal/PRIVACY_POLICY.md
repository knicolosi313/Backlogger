# Backlogger Privacy Policy

Last updated: November 8, 2026

Backlogger is operated by **zac2214** ("we", "us", or "our"). For privacy
questions or requests, contact **kln6267@rit.edu**.

This policy covers the Backlogger Chrome extension and its connected backend.
Backlogger records meeting audio, creates AI-assisted user stories, and helps
users review and export those stories to Trello or Jira Cloud.

## Information we handle

- **Meeting audio:** When you start recording, the extension captures audio from
  the selected browser tab and your microphone. It records audio, not video or
  screen images. You can also submit an audio file through the backend.
- **Meeting content:** Meeting titles, timestamps, speaker-labeled transcripts,
  source quotations, generated stories, acceptance criteria, solution tasks,
  assumptions, AI reviews, confidence scores, and your edits and review decisions.
- **Connected account information:** Trello member identifiers and usernames;
  Jira account identifiers and display names; authorization tokens, refresh
  tokens where applicable, and authorization-session information. Backlogger
  uses provider authorization rather than collecting your Trello or Atlassian
  account password.
- **Board and project context:** Identifiers, names, epic information, existing
  story or issue content, and destination metadata obtained through authorized
  Trello or Jira API calls. We may cache this context and numerical embeddings
  derived from story text to find related work and assess overlap.
- **Preferences and activity:** Backend URL, any API token you configure,
  selected account destinations, creativity and auto-import preferences,
  processing and export status, and story-review events used for backlog metrics.
- **Technical information:** Our hosting services may handle IP addresses,
  request paths, timestamps, browser/request metadata, and operational error logs
  when serving requests.

The page launcher checks the current Trello or Jira URL to decide whether to
show the Open Backlogger button. It does not scrape page contents or collect a
general browsing history. Board context is retrieved through provider APIs.

## How we use information

We use this information to record and play audio, transcribe meetings, generate
and review stories, identify related or overlapping work, maintain connections,
export stories, display processing and backlog metrics, and troubleshoot the
service. If you enable auto-import, eligible stories can be exported without an
individual approval click for each story.

The current application does not include advertising, advertising trackers, or
a feature for selling personal information. We do not use meeting or connected
account data for advertising. Data obtained through Chrome APIs is used for
Backlogger's user-facing features in accordance with the Chrome Web Store User
Data Policy, including its Limited Use requirements.

## AI processing and other service providers

Backlogger sends information to services needed to provide its features:

| Service | Information and purpose |
| --- | --- |
| Google Gemini API | Uploaded meeting audio for transcription; transcripts, generated stories, selected existing board content, and epic context for generation and review; story text for embeddings. |
| Supabase Storage | Audio files stored in the configured private media bucket. |
| TigerData / Timescale PostgreSQL | Meeting records, transcripts, drafts, reviews, cached context and embeddings, encrypted connection tokens, and processing/export records. |
| Railway | Backend hosting and request processing, including data passing through the backend and operational logs. |
| Trello and Atlassian Jira Cloud | Authorized account and board/project API requests, and stories, checklists or task descriptions, and evidence references that you choose or configure Backlogger to export. |

These descriptions reflect the intended hosted setup. A self-hosted deployment
may use different hosting or local audio storage; its operator controls that
deployment and should provide an appropriate privacy notice.

Google's handling of API inputs and outputs depends on the applicable Gemini
API service, billing status, region, and terms. Do not assume that AI submissions
are excluded from provider product improvement or human review. Consult the
[Gemini API terms](https://ai.google.dev/gemini-api/terms) and ask us about the
deployment configuration before submitting confidential or sensitive material.
Backlogger does not itself train a separate model on your meetings.

Provider processing, retention, and processing locations are governed by their
applicable terms and arrangements. Processing may occur outside your country.
Exported information is also subject to your Trello or Jira workspace permissions
and those services' policies.

## Workspace visibility and security

**The current version uses a shared backend workspace. It does not provide
application user accounts or per-user isolation for meetings, recordings,
transcripts, stories, or cached board context.** People who can reach the backend
may be able to list or retrieve this information. Browser-specific Trello/Jira
connections do not make meeting data private to that browser or account.

Do not submit confidential, regulated, or sensitive information, or recordings
that require private access controls, to this version. A private storage bucket
does not itself restrict access through the backend's audio endpoints.

Connection tokens are encrypted before database storage using a server-side
key. Authorization uses browser-session cookies. Hosted traffic should use HTTPS;
local development may use HTTP on localhost. Operators and service providers
with appropriate administrative access may access stored data. These measures
do not guarantee security or create user isolation.

## Browser storage and cookies

The extension stores settings and selected destinations in Chrome extension
storage. Recording and connection-tab state use browser-session storage. Pending
audio is held in the extension's IndexedDB so closing the panel does not lose an
unsubmitted recording. Pending audio can remain until it is cleared by the
application, replaced by a later recording, or you remove extension data.

The backend uses cookies to associate your browser with a Trello or Jira
connection and to validate authorization flows. These are functional cookies,
not advertising cookies. Clearing a cookie does not necessarily delete the
corresponding stored backend record.

## Retention and deletion

The current application has **no automatic expiry schedule** for uploaded
recordings, transcripts, stories, cached context or embeddings, and review/export
records. They may remain until an operator deletes them. Replacing uploaded
audio attempts to remove the previous file; this is not a general deletion
feature. Temporary backend files used to read remote audio are cleaned up after
use, but that does not delete the stored recording.

Disconnecting Trello or Jira removes that browser's saved connection record and
its stored tokens. It does not delete meetings, transcripts, cached board data,
or already exported cards/issues, and it does not necessarily revoke permission
at the provider. You can also revoke access in Trello or your Atlassian account.
Uninstalling the extension does not delete backend or provider-held information.

Contact **kln6267@rit.edu** to request access, correction, or deletion of data
held by this Backlogger deployment. Include enough information to identify the
meeting or board, but do not email passwords or access tokens. We may need to
verify your authority, particularly for recordings involving other people.
There is no self-service meeting-deletion endpoint in the current version.

Provider copies, logs, and backups may have separate retention rules and may not
be erased immediately when an active record is deleted. We cannot remove copies
controlled by another workspace or provider through a Backlogger disconnect.

## Your choices and rights

You can choose whether to record, stop recording, connect or disconnect accounts,
enable auto-import, and review stories before manual export. Notify participants
and obtain any required permission before recording or submitting their content
to AI processing. A reminder in the extension does not obtain consent for you.

Depending on your location and applicable law, you may have rights to access,
correct, delete, restrict, object to processing, or obtain a portable copy of
your personal information, and to withdraw consent where processing relies on
it. You may also have a right to complain to a data-protection authority.
Contact us to make a request or discuss the basis for processing your data.

## Children

Backlogger is not intended for children under 18. Do not knowingly submit
children's personal information. Contact us if you believe such information has
been submitted.

## Changes and contact

We will update this page and its date when this policy changes. Material changes
to extension data practices should also be disclosed in the application before
the changed collection or use begins.

Operator: **zac2214**  
Contact: **kln6267@rit.edu**
