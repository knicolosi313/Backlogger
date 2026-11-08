# Publishing Backlogger's policies

These documents describe the code reviewed on November 8, 2026. They are drafts
for operator review, not a determination of legal compliance. No public pages
or URLs have been created by adding these files.

- [Privacy Policy](PRIVACY_POLICY.md)
- [Terms of Service](TERMS_OF_SERVICE.md)

## Publish without a website

If this repository is public, each document's public GitHub file page can serve
as a readable policy document. Open each file on GitHub, copy its public URL,
and test it in a signed-out browser before entering it in Atlassian's app
settings. A URL in a private repository will not work for other users.

Alternatively, copy each document to a publicly published Notion page or a
separate public repository containing only the policies. No running web app is
needed. Never publish `.env`, credentials, recordings, or customer content.

When copying the documents elsewhere, replace the Terms' relative Privacy
Policy links with the actual public policy URL. Keep public copies in sync
with these source files. Acceptance of a particular URL format is up to the
platform reviewing the app.

## Review before public use

1. Confirm that **zac2214** is the correct operator identity and
   **kln6267@rit.edu** is a monitored public contact address. Add the full legal
   operator identity and address where applicable law requires them.
2. Confirm the actual hosting providers, regions, logging/backups, and Google
   Gemini billing/data-use configuration. Do not claim AI data is excluded from
   provider improvement unless the applicable configuration and terms support it.
3. Decide and implement appropriate retention and deletion procedures. The
   policy currently discloses no automatic expiry and manual deletion requests;
   it does not promise a retention period that the code cannot enforce.
4. The current unauthenticated meeting APIs expose shared-workspace data.
   Disclosing this is not a substitute for access controls. Add authentication
   and authorization before accepting private customer meetings, then revise
   these documents to match.
5. Establish applicable processing grounds, required recording notices/consent,
   provider agreements and international-transfer arrangements, and request
   handling for the places and users you serve. Get legal review appropriate to
   your planned use; these drafts do not establish regulatory compliance.
6. Match any Chrome Web Store listing and privacy disclosures to actual behavior,
   including audio capture, AI transfers, board context, and retained embeddings.

Reference guidance used while drafting:

- [Chrome Web Store privacy-policy requirements](https://developer.chrome.com/docs/webstore/program-policies/privacy)
- [Chrome Web Store Limited Use requirements](https://developer.chrome.com/docs/webstore/program-policies/limited-use)
- [ICO guidance on privacy notices](https://ico.org.uk/for-organisations/advice-for-small-organisations/privacy-notices-and-cookies/how-to-write-a-privacy-notice-and-what-goes-in-it)
- [Gemini API terms](https://ai.google.dev/gemini-api/terms)
