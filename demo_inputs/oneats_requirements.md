OneATS Admin Console — User Stories
Complete set of user stories for the OneATS Admin Console, reflecting the current admin prototype. Generated June 19, 2026. Each story includes its acceptance criteria and a link to the corresponding Jira issue in the OneATS (ON) project.

US-1.1 — View organization-wide performance at a glance
As an admin, I want a dashboard summarizing open requisitions, active candidates, placements this month, and pending approvals, so that I can assess business health without navigating to each module.
Acceptance criteria: Dashboard loads by default on login; each stat tile shows a current value and a week-over-week delta; tiles are clickable shortcuts into the relevant module.
Jira: ON-42 — US-1.1 — View organization-wide performance at a glance

US-1.2 — Approve or reject pending closures and offers from one queue
As an admin, I want a single Approval Queue listing placement closures and offers awaiting sign-off, so that I can act on them without hunting through individual candidate records.
Acceptance criteria: Each queue item shows candidate, role, client, margin/comp, and submitting recruiter; Approve and Reject (or Send Back) actions update the item's status inline and remove it from the "pending" count; all approval/rejection actions are logged with timestamp and approving admin.
Jira: ON-43 — US-1.2 — Approve or reject pending closures and offers from one queue

US-2.1 — Create a new requisition
As an admin or recruiter, I want to create a new requisition with title, client, description, required skills, and assigned recruiter, so that the role enters the pipeline and becomes visible to the team.
Acceptance criteria: Required fields are validated before submission; a new row appears in the Requisitions table immediately with status "Open."
Jira: ON-44 — US-2.1 — Create a new requisition

US-2.2 — Auto-publish new requisitions to Ceipal
As an admin, I want every newly created (or reopened) requisition automatically posted to Ceipal, so that I never have to duplicate data entry in a second system.
Acceptance criteria: On save, the system calls the Ceipal API to create the requisition and displays a non-blocking "Posting to Ceipal…" status indicator; on success the requisition is tagged "Ceipal Synced"; on failure the admin sees a retry option and the requisition is flagged "Sync Failed" rather than silently dropped.
Jira: ON-45 — US-2.2 — Auto-publish new requisitions to Ceipal

US-2.3 — Auto-syndicate to job boards via Ceipal
As an admin, I want requisitions synced to Ceipal to automatically distribute to Dice, ZipRecruiter, Indeed, and any other boards Ceipal is configured to publish to, so that candidate reach is maximized without manual posting to each board.
Acceptance criteria: Syndication is triggered automatically once the Ceipal sync succeeds (no separate manual step); the requisition detail view lists which boards it was distributed to and the timestamp of distribution; closing or pausing a requisition in OneATS propagates a close/pause request to Ceipal and downstream boards.
Jira: ON-46 — US-2.3 — Auto-syndicate to job boards via Ceipal

US-2.4 — See sync and AI-review status at a glance
As an admin, I want each requisition row to show its open/closed state, AI review outcome, and Ceipal sync state as status pills, so that I can scan the whole list for anything needing attention.
Acceptance criteria: Pills update in real time as each background process (AI review, Ceipal sync) completes; a requisition that fails Ceipal sync is visually distinct (e.g., red/amber) from one still in progress.
Jira: ON-47 — US-2.4 — See sync and AI-review status at a glance

US-2.5 — Manually re-run AI matching against an existing requisition
As an admin, I want an "AI Match" action on any requisition, so that I can re-scan the resume database against it after the candidate pool has grown or the requisition changed.
Acceptance criteria: Clicking AI Match shows a scanning indicator referencing the number of resumes being scanned, then opens the AI Match Results view for that requisition.
Jira: ON-48 — US-2.5 — Manually re-run AI matching against an existing requisition

US-3.1 — Automatically score every applicant against the requisition they applied to
As an admin, I want every resume — whether submitted internally, sourced from the database, or received via Ceipal/Dice/ZipRecruiter/Indeed — automatically scored against the requisition's required skills and description, so that no applicant has to be manually screened before a first-pass evaluation exists.
Acceptance criteria: Scoring runs automatically the moment an application/resume is associated with a requisition (no manual trigger required for inbound applicants); the score is a 0–100% value persisted on the candidate-requisition pairing; scoring factors include required-skill coverage and job-description/resume text relevance.
Jira: ON-49 — US-3.1 — Automatically score every applicant against the requisition they applied to

US-3.2 — Sort matched candidates into consistent tiers
As an admin, I want scored candidates automatically split into Tier 1 (90–100%, Excellent Match), Tier 2 (75–89%, Strong Match), and Tier 3 (below 75%, Possible Match), so that recruiters can prioritize outreach without re-reading every resume.
Acceptance criteria: Tier thresholds (90/75) are configurable at the system level, not hardcoded per page; the same thresholds and labels are used consistently across the Admin, Recruiter, and Candidate experiences; the AI Match Results view groups candidates into clearly labeled tier sections.
Jira: ON-50 — US-3.2 — Sort matched candidates into consistent tiers

US-3.3 — See exactly which required skills matched and which are missing
As an admin, I want each AI match result to show the specific required skills a candidate has (matched) and lacks (gap), so that I can quickly judge whether a near-miss is worth pursuing.
Acceptance criteria: Matched and missing skills render as distinct, visually separated tags on each candidate card and on the candidate's resume detail view; the breakdown is specific to the requisition currently being viewed, not a generic score.
Jira: ON-51 — US-3.3 — See exactly which required skills matched and which are missing

US-3.4 — Distinguish externally sourced applicants from internal pipeline candidates
As an admin, I want candidates who applied via Ceipal or an external job board to be visually flagged (e.g., source badge), so that I can track which boards are producing qualified applicants.
Acceptance criteria: Source badge indicates the originating channel (Ceipal direct, Dice, ZipRecruiter, Indeed, internal); badge is visible both in match result lists and on the candidate's resume view.
Jira: ON-52 — US-3.4 — Distinguish externally sourced applicants from internal pipeline candidates

US-3.5 — Drill into a candidate's resume directly from match results
As an admin, I want to open a candidate's full resume and match breakdown from within the AI Match Results view, so that I can evaluate and act on a candidate without losing my place in the results list.
Acceptance criteria: Resume view displays contact/role/location/experience summary, full skill list, and the score/matched/missing breakdown against the job currently in context; a "Send" or "Submit" action is available directly from this view.
Jira: ON-53 — US-3.5 — Drill into a candidate's resume directly from match results

US-4.1 — Maintain a master candidate list across all recruiters
As an admin, I want a single searchable list of all candidates regardless of which recruiter owns them, so that I have organization-wide visibility into the talent pool.
Acceptance criteria: List shows name, location, target role, owning recruiter, current status, and last activity timestamp; clicking a candidate opens their full profile.
Jira: ON-54 — US-4.1 — Maintain a master candidate list across all recruiters

US-4.2 — Track every submission across clients and recruiters
As an admin, I want a dedicated view of all candidate submissions to clients (job, client, recruiter, submission date, status), so that I can monitor pipeline velocity and identify stalled submissions.
Acceptance criteria: Submissions list is filterable by status and sortable by submission date; status values stay in sync with the candidate's record in the master candidate list.
Jira: ON-55 — US-4.2 — Track every submission across clients and recruiters

US-4.3 — Add a new candidate manually
As an admin, I want to manually add a candidate record, so that referrals or offline sourcing can be entered into the system alongside auto-ingested applicants.
Acceptance criteria: New candidate immediately appears in the All Candidates list with status "New" and can subsequently be AI-matched against open requisitions.
Jira: ON-56 — US-4.3 — Add a new candidate manually

US-5.1 — Surface likely duplicate candidate records
As an admin, I want the system to flag candidate pairs that are likely duplicates (matching phone, email domain, or near-identical resume content), so that data quality issues are caught before they cause confusion in the pipeline.
Acceptance criteria: Each flagged pair shows a similarity percentage and the basis for the match (e.g., "same phone number," "identical skill set"); pairs list the recruiter(s) who own each record.
Jira: ON-57 — US-5.1 — Surface likely duplicate candidate records

US-5.2 — Resolve duplicate flags
As an admin, I want to either merge two flagged records into one or dismiss the flag as "not a duplicate," so that I can clean up data without losing legitimate distinct candidates.
Acceptance criteria: Merge consolidates submission/interview/placement history under a single candidate record; dismissing a flag suppresses it from reappearing for that specific pair.
Jira: ON-58 — US-5.2 — Resolve duplicate flags

US-6.1 — View all scheduled interviews in one place
As an admin, I want a consolidated view of interviews across all candidates and recruiters, so that I can spot scheduling conflicts or gaps.
Acceptance criteria: List/calendar view shows candidate, role, client, interviewer, and date/time; supports rescheduling.
Jira: ON-59 — US-6.1 — View all scheduled interviews in one place

US-6.2 — Approve offers before they go out
As an admin, I want to review and approve or send-back offers before they're communicated to a candidate, so that compensation and terms are vetted at the org level.
Acceptance criteria: Offers awaiting approval surface in both the dashboard queue and a dedicated tab; approving an offer marks it "Sent" and timestamps the approval.
Jira: ON-60 — US-6.2 — Approve offers before they go out

US-6.3 — Track NDA and document signature status
As an admin, I want to see which candidates have pending or completed NDA/document signatures, so that placements aren't held up by missing paperwork.
Acceptance criteria: Status (e.g., Sent, Signed, Overdue) is visible per candidate; reminders can be triggered for overdue signatures.
Jira: ON-61 — US-6.3 — Track NDA and document signature status

US-7.1 — Track confirmed placements
As an admin, I want a placements view showing closed candidates, their client, start date, and margin, so that I can report on monthly placement performance.
Acceptance criteria: Placement count matches the dashboard's "Placements This Month" tile; each placement links back to the originating requisition and candidate record.
Jira: ON-62 — US-7.1 — Track confirmed placements

US-8.1 — Centralize candidate and placement documents
As an admin, I want a documents hub for resumes, signed offers, and NDAs, so that paperwork is retrievable without digging through individual candidate threads.
Acceptance criteria: Documents are filterable by candidate, requisition, or document type; access is restricted based on user role/permissions.
Jira: ON-63 — US-8.1 — Centralize candidate and placement documents

US-9.1 — Manage internal users and roles
As an admin, I want to view and manage internal users (recruiters, admins) and their access levels, so that permissions stay aligned with each person's responsibilities.
Acceptance criteria: User list shows name, role, and status (active/inactive); admin can change a user's role or deactivate access.
Jira: ON-64 — US-9.1 — Manage internal users and roles

US-9.2 — Manage personal account details
As an admin, I want to view and edit my personal account details (full name, email, title, organization, phone, and location) from a dedicated Settings page, so that my profile information stays accurate without needing to contact support.
Acceptance criteria: Settings page displays an Account section with editable fields for full name, email, title, organization, phone, and location; a Save Changes action persists edits and a Reset action reverts unsaved changes; changes only affect the current admin's own profile and are visible immediately in the topbar/avatar where applicable.
Jira: ON-65 — US-9.2 — Manage personal account details

US-9.3 — Personalize console appearance with accent color themes and dark mode
As an admin, I want to switch the console's accent color and toggle dark mode independently from Settings, so that I can personalize how OneATS looks for me without affecting how my teammates see it.
Acceptance criteria: Settings page offers an Accent Colour picker with five options (Gold, Steel Blue, Sage, Terracotta, Plum), each applied instantly across navigation, buttons, pills, and highlights; a separate Dark Mode toggle switches backgrounds and surfaces to a dark theme and works correctly with any of the five accent colors; the selected accent color and dark/light mode persist across sessions for that user; switching theme or mode never changes the underlying data, layout, or organization of any page.
Jira: ON-66 — US-9.3 — Personalize console appearance with accent color themes and dark mode

OneATS — Candidate Portal
User Story Backlog
Prepared for the development team, based on the Candidate Portal prototype (candidate_portal_prototype.html). Stories are in standard "As a / I want / so that" form with acceptance criteria. Each [ON-XXX] link opens the corresponding Jira ticket in the OneATS project.
Epic 1: Dashboard  ·  ON-93
US-1.1  [ON-100]  —  See a personal at-a-glance summary of my job search
As a candidate, I want a dashboard that surfaces my active applications, interviews scheduled, pending offers, and profile strength as stat tiles, so that I can assess where I stand without navigating to each section.
Acceptance criteria: Dashboard loads by default on login; each tile shows a current count and a human-readable sub-label (e.g., "Next: Tomorrow, 10:00 AM"); tiles are clickable shortcuts into the relevant section. An "Action needed" indicator appears on the Offers tile whenever a pending offer exists.
US-1.2  [ON-101]  —  Review my recent applications from the dashboard
As a candidate, I want a "My Applications" summary card on the dashboard showing my four most recent applications with role, company, submission date, and status pill, so that I can see my pipeline health without leaving the home screen.
Acceptance criteria: Up to four most recent applications are displayed; status pills use consistent color coding (Amber = Interviewing, Green = Offer Extended, Blue = Submitted, Red = Not Selected); a "View submission history ->" link navigates to the full Applications page.
US-1.3  [ON-102]  —  See my upcoming interviews at a glance
As a candidate, I want an upcoming interviews card on the dashboard showing the next two scheduled interviews with date, role, company, time, and interview type, so that I can prepare without hunting through a separate calendar.
Acceptance criteria: Interviews are sorted chronologically; each entry shows date badge, role title, company, time window, and a type tag (Video / Onsite / Phone); a "View all ->" link navigates to the Interviews page.
US-1.4  [ON-103]  —  Access my profile and resume directly from the dashboard
As a candidate, I want profile and resume summary cards on the dashboard showing my completion ring, skills tags, and current resume file with AI analysis status, so that I can spot gaps and take action without leaving the home screen.
Acceptance criteria: Profile card shows name, title, location, completion percentage ring, and top skills; Resume card shows file name, upload date, size, and an "AI Analyzed" badge when analysis is complete; an AI cover-letter prompt appears if a draft is ready for a matched role, with a "Review & send ->" link.
US-1.5  [ON-104]  —  See and act on pending offers from the dashboard
As a candidate, I want an offers & documents summary card on the dashboard showing any active offer and any documents awaiting my signature, so that I can act on time-sensitive items without navigating away.
Acceptance criteria: Offer card displays role, company, expiry date, and a status pill; primary CTA ("View & Accept Offer") and secondary CTA ("Decline") are both visible; documents awaiting signature each show a "Sign Now" button; completed documents show a green "Completed" badge.
Epic 2: Profile & Resume  ·  ON-94
US-2.1  [ON-105]  —  View and edit my profile information
As a candidate, I want a Profile page showing my name, title, location, email, phone, and target role, with an Edit button that opens an inline modal, so that my information stays current for recruiters and the matching system.
Acceptance criteria: Clicking "Edit ->" opens a modal pre-filled with current values; fields include Email, Phone, Location, and Target Role; saving updates all display locations simultaneously (Profile page, Settings page, and top-bar); no full-page reload is required.
US-2.2  [ON-106]  —  See my profile strength and know how to improve it
As a candidate, I want a visual profile-strength indicator (completion ring and percentage) with a specific, actionable tip for reaching the next milestone, so that I know exactly what to do to make my profile more competitive.
Acceptance criteria: Completion ring renders the current percentage as a conic gradient; the tip is specific (e.g., "Add 2 more skills to reach 90%"), not generic; percentage and tip update in real time after profile edits or skill additions.
US-2.3  [ON-107]  —  Add skills to my profile
As a candidate, I want an "+ Add skill" control on my Profile page that opens a modal where I can type and save a new skill, so that my skill set stays accurate and improves my job-match scores.
Acceptance criteria: Clicking "+ Add skill" opens a modal with a text input; submitting a non-empty value appends the skill tag to the skills list immediately; the modal closes on save; new skills are reflected in profile-strength calculation.
US-2.4  [ON-108]  —  Upload or replace my resume
As a candidate, I want a "Replace ->" control on my Profile page that lets me upload a new resume file to replace the current one, so that recruiters and the AI matching engine always have my most up-to-date experience.
Acceptance criteria: After upload, the file name, upload date, and file size update immediately; the previous resume is superseded; the system triggers AI analysis and updates the "AI Analyzed" badge once complete; supported formats include PDF and DOCX.
US-2.5  [ON-109]  —  See my resume's AI analysis results
As a candidate, I want an AI analysis note on my resume card showing which role has a cover letter ready and the match percentage that drove it, so that I understand how the system has evaluated my resume and can act on the output.
Acceptance criteria: The AI note appears only after analysis is complete; it names the specific role and company, cites the match percentage, and provides a "Review & send ->" link to the cover-letter modal; the badge reads "AI Analyzed" in green.
Epic 3: AI-Generated Cover Letter  ·  ON-95
US-3.1  [ON-110]  —  Review an AI-drafted cover letter before sending
As a candidate, I want a cover-letter modal that displays a full AI-drafted letter pre-filled with my name, the target role, company, and a personalized body paragraph drawn from my resume and the job's requirements, so that I can verify the content before it goes to a recruiter.
Acceptance criteria: Modal header shows role and company plus a match percentage; an "AI-drafted" badge is displayed; the letter body is fully visible and readable in a styled document block; "Edit Letter" and "Send Application" CTAs are present; closing the modal does not discard the draft.
US-3.2  [ON-111]  —  Send my application with the AI cover letter in one step
As a candidate, I want a "Send Application" button in the cover-letter modal that submits my application along with the drafted letter, so that I can move from reviewing to applying without switching screens.
Acceptance criteria: Clicking "Send Application" closes the modal and triggers submission; the application appears in My Applications with status "Submitted"; a cover letter is associated with the application record.
US-3.3  [ON-112]  —  Receive cover letter drafts for every AI-matched role above a threshold
As a candidate, I want the system to automatically generate a cover-letter draft for each role where my AI match score meets or exceeds the system threshold (currently 80%), so that I always have a personalized draft ready when I decide to apply.
Acceptance criteria: Drafts are generated without manual triggering; each draft is specific to the role and company, not a generic template; accessible from both the dashboard resume card and the matched-jobs card. Threshold must come from shared config (not hardcoded), consistent with Admin/Recruiter tier thresholds.
Epic 4: My Applications  ·  ON-96
US-4.1  [ON-113]  —  View the full history of roles I have applied to
As a candidate, I want an Applications page listing every role I have applied for, with the role title, company, submission date, and current status, so that I have a single source of truth for all my application activity.
Acceptance criteria: All applications are listed regardless of status; columns include Role, Company, Submitted date, and Status pill; list is sorted by most recent submission by default; no pagination limit prevents older applications from appearing.
US-4.2  [ON-114]  —  See real-time status updates on each application
As a candidate, I want status pills on my Applications page that reflect the current stage of each application (Submitted, Interviewing, Offer Extended, Not Selected), so that I always know where I stand without having to contact the recruiter.
Acceptance criteria: Status values are: Submitted (blue), Interviewing (amber), Offer Extended (green), Not Selected (red); statuses update automatically when the recruiter advances or closes my application; no manual refresh is required.
US-4.3  [ON-115]  —  View the job description and required skills for any application
As a candidate, I want a clickable role title on my Applications page that opens a job-details modal showing the job description and required skills for that role, so that I can quickly re-read the role requirements when preparing for interviews or follow-up conversations.
Acceptance criteria: Clicking the role title opens a modal with the full job description and a tagged skills list; modal includes "Close" to dismiss; accessible from both the dashboard applications card and the full Applications page.
Epic 5: Interviews  ·  ON-97
US-5.1  [ON-116]  —  View all my scheduled interviews in one place
As a candidate, I want an Interviews page listing all upcoming interviews with date badge, role, company, time window, and interview type, so that I can prepare and keep track of my schedule without relying on email threads.
Acceptance criteria: Each interview entry shows: date badge (day + month), role title, company, start-end time with timezone, and a color-coded type tag (Video = blue, Onsite = green, Phone = amber); interviews are sorted chronologically.
US-5.2  [ON-117]  —  Request a reschedule for an interview I cannot attend
As a candidate, I want a "Reschedule" button on each interview entry that opens a modal where I can submit my preferred date, preferred time, and an optional reason, so that I can request a new time without having to call or email the recruiter directly.
Acceptance criteria: Reschedule modal is pre-labelled with the interview title and current time; fields: Preferred Date (MM/DD/YYYY), Preferred Time (free text), Reason (optional); submitting sends the request to the recruiter and shows a confirmation: "Your reschedule request has been sent. The recruiter will follow up to confirm a new time."; the original interview slot remains on the calendar until the recruiter confirms the change.
Epic 6: Offers & Documents  ·  ON-98
US-6.1  [ON-118]  —  Review the full details of an offer before deciding
As a candidate, I want a "View & Accept Offer" button that opens a modal displaying the offer's base salary, start date, location, reporting line, and explanatory copy, so that I can make an informed decision without requesting a separate document from the recruiter.
Acceptance criteria: Offer modal shows: Base Salary, Start Date, Location, Reporting To; includes plain-language copy explaining what acceptance means; "Accept Offer" and "Close" buttons are both present; offer expiry date is visible on the offer card.
US-6.2  [ON-119]  —  Accept an offer in one click
As a candidate, I want an "Accept Offer" button inside the offer details modal that confirms my acceptance and updates the offer status, so that the recruiting team is notified immediately and onboarding can begin.
Acceptance criteria: Clicking "Accept Offer" replaces the modal body with a success confirmation; the offer status pill updates to "Accepted" on the Offers page; the offer card action buttons are replaced with a green "Offer Accepted" badge; the recruiter's system reflects the acceptance in real time.
US-6.3  [ON-120]  —  Decline an offer I do not wish to accept
As a candidate, I want a "Decline" button on the offer card that allows me to formally decline, so that the recruiter is notified and the role can be re-opened without delay.
Acceptance criteria: Clicking "Decline" prompts a confirmation step before submitting; on confirmation, the offer status updates to "Declined"; the recruiter is notified; the action is logged with a timestamp.
US-6.4  [ON-121]  —  Sign required documents (e.g., NDA) directly in the portal
As a candidate, I want a "Sign Now" button on any pending document that opens a signing modal with a document preview and a confirmation checkbox, so that I can complete onboarding paperwork without leaving OneATS or printing anything.
Acceptance criteria: Signing modal shows the document name, a document preview area, and a checkbox "I have reviewed this document and agree to its terms"; "Sign Document" is enabled only after the checkbox is checked; on confirmation, the document row updates to show "Signed [date]" and the "Sign Now" button is replaced with a green "Completed" badge.
US-6.5  [ON-122]  —  See a complete list of all my documents and their status
As a candidate, I want a Documents section on the Offers & Documents page listing every document associated with my applications, along with its status, so that I have a single place to track outstanding paperwork.
Acceptance criteria: Document rows display: document name, current status (Awaiting signature / Signed [date] / Completed); pending documents show a "Sign Now" CTA; completed documents show a green "Completed" badge; the list updates in real time after signing.
Epic 7: Settings & Account  ·  ON-99
US-7.1  [ON-123]  —  Update my personal information from Settings
As a candidate, I want a Settings page with a Personal Information form containing fields for Email, Phone, Location, and Target Role, so that I can keep my account details accurate without going through the profile page.
Acceptance criteria: Saving via "Save Changes" updates the same data as editing via the profile modal (single source of truth); a "Changes saved" confirmation message appears briefly after save; all other display locations (dashboard, profile page) reflect the update without a full-page reload.
US-7.2  [ON-124]  —  Switch the portal to dark mode
As a candidate, I want a Dark Mode toggle in Settings that switches the entire portal to a dark background while preserving all status colors and readability, so that I can use the portal comfortably in low-light environments.
Acceptance criteria: Toggle updates the UI immediately without a page reload; all surfaces, borders, cards, and text adapt to the dark-mode color palette; status pills (green, amber, blue, red) remain legible; the chosen mode persists across sessions (stored server-side, not solely localStorage).
US-7.3  [ON-125]  —  Choose an accent color that reflects my preference
As a candidate, I want an Accent Colour picker in Settings offering at least five themes (Gold, Steel Blue, Sage, Terracotta, Plum), so that the portal feels personalized without compromising readability.
Acceptance criteria: Clicking an accent card applies the theme immediately; the active theme card is highlighted with a border; accent color is applied consistently to navigation active states, primary buttons, profile ring, and AI badges; theme persists across sessions (stored server-side); dark-mode and accent-color selections work independently of each other.
Cross-Cutting Notes for Engineering
•  Single source of truth for profile data: Email, Phone, Location, and Target Role must be stored once and rendered consistently on the Dashboard, Profile page, and Settings form. Editing via any surface must update all surfaces synchronously.
•  Consistent status vocabulary: Application status values (Submitted, Interviewing, Offer Extended, Not Selected, Accepted, Declined) and their color pills must match exactly across all portals. These values should live in a shared enum/constant, not be hardcoded per component.
•  AI match thresholds shared across portals: Cover-letter generation triggers and match score tiers (e.g., the 80% threshold for auto-drafting) must use the same config that drives tiering in the Admin and Recruiter portals (see Admin US-3.2). Do not duplicate thresholds.
•  Real-time status updates: Offer status, document signature status, and application status must update without a manual page refresh. Use WebSockets or polling so candidates see recruiter actions promptly.
•  Session persistence for appearance settings: Dark mode and accent color preferences must persist across sessions. Store in user account preferences server-side (not solely localStorage) so settings survive clearing browser data.
•  Notification badge: The bell icon in the top bar shows a red dot for unread notifications (e.g., new offer, interview scheduled, document request). Engineering must define a notifications model that feeds this indicator and clears it on acknowledgment.




OneATS
Product Requirements Document
Version 1.0  |  June 2026
Interonit Solutions





Status
Draft for Review
Author
Product Team, Interonit Solutions
Last Updated
June 23, 2026
Jira Project
ON (OneATS)









Table of Contents



1. Executive Summary
OneATS is a purpose-built Applicant Tracking System for Interonit Solutions, a staffing and recruiting firm. It replaces fragmented manual workflows with a unified, AI-powered platform serving three user groups: Admins, Recruiters, and Candidates.
The platform automates the most labor-intensive parts of the recruiting lifecycle — requisition publishing, candidate sourcing and scoring, interview scheduling, offer management, and compliance paperwork — while providing each user type a tailored experience through dedicated portals.
OneATS integrates natively with Ceipal (the firm’s existing staffing platform) for requisition syndication and external applicant ingestion, and with Dice, ZipRecruiter, and Indeed for job-board distribution.

Key Outcomes
  •  Eliminate manual re-entry of requisitions across Ceipal and job boards
  •  Surface best-fit candidates instantly via AI scoring on every new application
  •  Give candidates a self-service portal to track status, accept offers, and sign documents
  •  Provide admins full organizational visibility with approval controls and deduplication
  •  Ship a production-ready v1 against 66 Jira stories across three portals


2. Problem Statement & Goals
2.1  Problems Being Solved
Interonit Solutions currently operates with Ceipal for ATS functions and manual processes for candidate matching, interview scheduling, and offer management. This creates the following pain points:
	•	Recruiters must duplicate data entry — creating requisitions in Ceipal and separately on each job board.
	•	Candidate matching is entirely manual; there is no automated scoring of applicants against requisition requirements.
	•	Candidates have no self-service visibility into application status, interview schedule, or offer details. All communication goes through email.
	•	Admins lack a single pane of glass — approvals, placements, submissions, and duplicates are scattered across systems.
	•	Duplicate candidate records accumulate silently, creating confusion during submissions and offers.
	•	Offer approvals and NDA signing require back-and-forth email threads with no audit trail.

2.2  Goals for Version 1
Goal
Success Metric
Automate requisition publishing
100% of submitted reqs auto-publish to Ceipal within 60 s
AI-score all inbound applicants
Every applicant has a match score before recruiter first-view
Self-service candidate portal
Candidates can accept offers and sign NDAs without email
Admin approval workflow
All offers and closures pass through a single approval queue
Duplicate detection
Duplicate pairs flagged automatically with similarity basis
Personalization (dark mode + themes)
Per-user settings persist across sessions (server-side)

3. Users & Personas
OneATS serves three primary user types, each with a dedicated portal.

3.1  Admin
Attribute
Detail
Role
Recruiting Operations Lead / Managing Partner
Responsibilities
Org-wide visibility; approve offers and closures; manage users; resolve duplicate records; track placements and margins
Pain Points
Blind spots across recruiters’ pipelines; no unified approval queue; manual deduplication; no placement dashboard
Needs
Single dashboard for all KPIs; one-click approve/reject; AI match management; duplicate merge; role-based access control

3.2  Recruiter
Attribute
Detail
Representative
Jordan Patel, Recruiter at Interonit Solutions
Responsibilities
Source candidates; create and manage requisitions; schedule interviews; submit candidates to clients; track offers
Pain Points
Manual job-board posting; no automated scoring of Ceipal applicants; interview scheduling via email; no submission log
Needs
One-form requisition creation with auto-syndication; instant AI tier view; in-app interview scheduling and feedback; offer status tracking

3.3  Candidate
Attribute
Detail
Profile
Active job seeker applied to or sourced for Interonit-managed roles
Responsibilities
Upload resume; track applications; attend interviews; review and sign offers and NDAs
Pain Points
No visibility into application status; interview details only via email; offer paperwork via email attachments
Needs
Real-time application status; self-service reschedule requests; in-portal offer review, acceptance, and document signing

4. System Architecture Overview
4.1  Portals
OneATS consists of three web-based portals sharing a common backend API and data layer:
	•	Admin Console — Full operational control; org-wide data access.
	•	Recruiter Portal — Scoped to the logged-in recruiter’s candidates and requisitions, with read access to org-wide requisitions.
	•	Candidate Portal — Self-service; data scoped to the authenticated candidate only.

4.2  External Integrations
System
Direction
Purpose
Ceipal
Outbound (req publish) + Inbound (applicant sync)
Primary ATS bridge; requisition distribution and applicant ingestion
Dice
Outbound via Ceipal
Job board distribution for open requisitions
ZipRecruiter
Outbound via Ceipal
Job board distribution for open requisitions
Indeed
Outbound via Ceipal
Job board distribution for open requisitions
Jira (ON)
Internal tracking
All user stories tracked as ON-XX issues in the OneATS project

4.3  AI / Matching Engine
The AI matching engine is a core internal service responsible for:
	•	Scoring every candidate-requisition pairing on a 0–100% scale using required-skill coverage and job-description/resume text relevance.
	•	Classifying scores into three tiers: Tier 1 — Excellent Match (≥90%), Tier 2 — Strong Match (75–89%), Tier 3 — Possible Match (<75%).
	•	Automatically drafting personalized cover letters for candidates whose match score meets or exceeds 80%.
Tier thresholds are stored in shared configuration — not hardcoded per component — and are applied consistently across all three portals.

4.4  Real-Time Updates
Application status, offer status, document signature status, and approval queue counts must update across portals without manual page refresh. The platform must implement WebSockets or server-sent events (SSE) to push state changes to connected clients in real time.

4.5  Personalization & Session Persistence
Each user may independently configure a dark/light mode toggle and one of five accent color themes (Gold, Steel Blue, Sage, Terracotta, Plum). These preferences are stored server-side so they persist across browser sessions and devices.

5. Feature Specifications
Features are organized by portal and epic. Each section cites the linked Jira tickets for full acceptance criteria.
5.1  Admin Console
The Admin Console gives operations leads full organizational visibility and control. It is divided into eight functional areas.
5.1.1  Dashboard (E1)
The dashboard is the default landing page for admin logins. It surfaces four stat tiles — Open Requisitions, Active Candidates, Placements This Month, and Pending Approvals — each with a current value and a week-over-week delta. Tiles are clickable shortcuts into the relevant module. An Approval Queue widget allows admins to approve or reject placements and offers inline, with all actions logged with a timestamp and the approving admin’s identity. (ON-42, ON-43)
5.1.2  Job Requisitions (E2)
Admins can create requisitions with title, client, description, required skills, and assigned recruiter. On save, the system auto-publishes to Ceipal (non-blocking, with sync status indicators) and Ceipal syndicates to all configured job boards. Requisition rows display status pills for open/closed state, AI-review outcome, and Ceipal sync state. Admins can manually re-trigger AI matching against any requisition after the candidate pool grows. (ON-44 through ON-48)
5.1.3  AI Matching (E3)
Every inbound resume — from internal submissions, Ceipal, or job boards — is automatically scored the moment it is associated with a requisition. No manual trigger is required for inbound applicants. Scored candidates are sorted into Tiers 1, 2, and 3 using shared configuration thresholds. Each match result shows matched and missing skills as distinct tags. Externally sourced applicants carry a source badge (Ceipal Direct, Dice, ZipRecruiter, Indeed, Internal) visible in all list and detail views. Admins can drill into a candidate’s full resume and match breakdown from within the AI Match Results view without losing their place in the list. (ON-49 through ON-53)
5.1.4  Candidate Management (E4)
A single, searchable master candidate list spans all recruiters, showing name, location, target role, owning recruiter, current status, and last activity. A dedicated submissions view shows all candidate submissions with client, recruiter, submission date, and status. Admins can add candidates manually. (ON-54 through ON-56)
5.1.5  Duplicate Detection (E5)
The system flags candidate pairs that match on phone number, email domain, or near-identical resume content, surfacing a similarity percentage and the basis for the match. Admins can merge two records (consolidating all submission, interview, and placement history) or dismiss a flag as not a duplicate (suppressing the pair from reappearing). (ON-57, ON-58)
5.1.6  Interviews & Offers (E6)
Admins have a consolidated view of all interviews across candidates and recruiters, with support for rescheduling. Offers are reviewed and approved or sent back before being communicated to candidates; approved offers are timestamped. NDA and document signature status is tracked per candidate with the ability to trigger reminders for overdue signatures. (ON-59 through ON-61)
5.1.7  Placements (E7)
A placements view shows closed candidates with client, start date, and margin. The placement count is consistent with the dashboard tile. Each placement links back to the originating requisition and candidate record. (ON-62)
5.1.8  Documents Hub (E8)
A centralized documents hub stores resumes, signed offers, and NDAs. Documents are filterable by candidate, requisition, or document type. Access is restricted by role. (ON-63)
5.1.9  User & Account Management (E9)
Admins can view and manage internal users, changing roles and deactivating access. Each admin can edit their own profile (full name, email, title, organization, phone, location). Per-user appearance settings (accent color, dark mode) are available from the Settings page. (ON-64 through ON-66)

5.2  Recruiter Portal
The Recruiter Portal is scoped to the logged-in recruiter’s pipeline. MoSCoW priorities are indicated for each story group.
5.2.1  Dashboard (E1)
The dashboard shows four stat cards — Assigned Requisitions, Submitted This Month, Interviews This Week, and Submission-to-Offer Rate — with trend indicators. It also surfaces the recruiter’s five most recent open/draft requisitions, four most recent submissions, and a mini calendar widget for upcoming interviews. A ‘+ New Requisition’ shortcut is available from the dashboard header. (ON-73, ON-74) [Must Have / Should Have]
5.2.2  Job Requisitions (E2)
Recruiters can view all requisitions org-wide in a searchable, filterable table. Clicking ‘View’ opens a detail modal with the full job description, rate card, required skills, and any AI-matched external applicants. Recruiters can create new requisitions (triggering auto-publishing and syndication) or save drafts. They can edit their own requisitions; edits to live reqs trigger a re-sync to Ceipal. Closed requisitions can be re-opened. (ON-75 through ON-79) [Must Have]
5.2.3  Candidates & Submissions (E3)
The Candidates section has three tabs: My Candidates (owned by the recruiter, with status pills), Submission History (all-time submissions log), and External Applicants (Ceipal-sourced with AI match scores and tier labels). Candidates can be viewed in a full profile modal showing contact details, resume, cover letter, and AI match banner. Recruiters can reject candidates from the profile modal. (ON-80 through ON-84) [Must Have]
5.2.4  Interviews (E4)
Recruiters view upcoming and past interviews in a sorted table. Interviews can be scheduled from the Interviews page header or directly from a candidate’s profile modal. Rescheduling updates the existing record in place. Completed interviews that still need feedback appear in a Feedback Pending tab; the structured feedback form captures a 1–5 star rating, a recommendation level, and free-text notes. (ON-85 through ON-88) [Must Have]
5.2.5  Offers (E5)
The Offers page shows all candidates in an offer stage for the recruiter, with status pills (Not Yet Offered, Awaiting Candidate Response, Accepted, Declined, Rescinded). Status values are updated in real time when candidates act through the Candidate Portal. (ON-89) [Must Have]
5.2.6  Settings & Personalization (E6)
Recruiters can edit their profile and toggle dark mode and accent color themes from Settings > Appearance. Preferences persist across sessions. (ON-90 through ON-92) [Should Have / Could Have]

5.3  Candidate Portal
The Candidate Portal is a self-service experience for job seekers, organized into seven epics.
5.3.1  Dashboard (E1)
The candidate dashboard shows stat tiles for active applications, scheduled interviews, pending offers, and profile strength. It surfaces a four-item recent applications card, an upcoming interviews card (next two), profile and resume summary cards, and an offer/documents action card. An action-needed indicator appears on the Offers tile whenever a pending offer exists. (ON-100 through ON-104)
5.3.2  Profile & Resume (E2)
Candidates can edit their profile via an inline modal that updates all display locations simultaneously without a full-page reload. A visual profile-strength ring shows the completion percentage with a specific actionable tip. Candidates can add skills and upload a new resume (superseding the previous file and triggering AI analysis). The AI analysis note identifies the best-matched role and the match percentage. (ON-105 through ON-109)
5.3.3  AI-Generated Cover Letters (E3)
For every role where a candidate’s AI match score meets or exceeds 80%, the system automatically drafts a personalized cover letter. The candidate reviews the draft in a modal, may edit it, and submits it with their application in a single click. The 80% threshold is derived from the same shared configuration that drives tiering in the Admin and Recruiter portals. (ON-110 through ON-112)
5.3.4  My Applications (E4)
An Applications page lists every role the candidate has applied for with real-time status pills (Submitted, Interviewing, Offer Extended, Not Selected). Clicking a role title opens a job-details modal showing the full job description and required skills. (ON-113 through ON-115)
5.3.5  Interviews (E5)
An Interviews page lists all upcoming interviews sorted chronologically with date badge, role, company, time window, and a color-coded type tag. Candidates can submit a reschedule request; the original slot remains until the recruiter confirms the change. (ON-116, ON-117)
5.3.6  Offers & Documents (E6)
Candidates can view full offer details (base salary, start date, location, reporting line) and accept or decline in-portal. Accepting immediately notifies the recruiter and updates the offer status. Document signing (NDAs) is handled in-portal via a modal with a document preview and a confirmation checkbox; signed documents are logged with a timestamp. (ON-118 through ON-122)
5.3.7  Settings & Account (E7)
Candidates can update personal information from the Settings page — all fields share a single source of truth with the Profile page. Dark mode and accent color themes are available; preferences persist server-side. (ON-123 through ON-125)

6. Cross-Cutting Requirements
These requirements span all three portals and must be enforced at the architecture and API layer, not per-component.

Requirement
Specification
Single source of truth
Profile data (Email, Phone, Location, Target Role) is stored once and rendered consistently across all portals. Editing via any surface must update all surfaces synchronously.
Consistent status vocabulary
Application status values (Submitted, Interviewing, Offer Extended, Not Selected, Accepted, Declined) and their color pills must match exactly across all portals. These values must live in a shared enum, not be hardcoded per component.
Shared AI thresholds
Tier thresholds (90/75), the cover-letter trigger (80%), and tier labels must be read from shared configuration. No portal may hardcode these values independently.
Real-time updates
Offer status, document signature status, and application status must update without manual page refresh. Use WebSockets or SSE polling so candidates and recruiters see each other’s actions promptly.
Session-persisted preferences
Dark mode and accent color preferences must persist across sessions. Store in user account settings server-side, not solely localStorage, so settings survive clearing browser data.
Notifications
A bell icon in the top bar shows a red badge for unread notifications (new offer, interview scheduled, document request). Engineering must define a notifications data model that feeds this indicator and clears it on acknowledgment.
Ceipal sync failure handling
A failed Ceipal sync must never silently drop a requisition. The record must be flagged ‘Sync Failed’ with a retry option visible to the creator.
Role-based access control
Admins see org-wide data. Recruiters see their own candidates and all requisitions (read). Candidates see only their own data. Document access is also restricted by role.

7. Non-Functional Requirements
Category
Requirement
Target
Performance
Dashboard load time
< 2 s on standard broadband
Performance
Ceipal sync latency
Non-blocking; success/failure indicator within 60 s
Performance
AI scoring
Score persisted before recruiter first-views the applicant
Availability
Uptime
99.5% monthly SLA across all portals
Security
Authentication
All portals require authenticated sessions; HTTPS only
Security
Data isolation
Candidate data is never exposed cross-candidate; API enforces row-level scoping
Accessibility
Color contrast
Status pills remain WCAG AA compliant in both light and dark mode across all five accent themes
Browser support
Target browsers
Latest two versions of Chrome, Safari, Firefox, and Edge
Audit logging
Approval actions
Every approve/reject/send-back action logged with actor, timestamp, and affected record

8. Release Scope — MoSCoW
Priority
Epic / Area
Portal(s)
Stories
Must Have
Dashboard, Requisitions, Candidates, Interviews, Offers
Admin, Recruiter, Candidate
ON-42–56, ON-73–89, ON-93–122
Must Have
AI Matching & Scoring
Admin, Recruiter, Candidate
ON-49–53, ON-83
Must Have
Ceipal Integration (publish + ingest)
Admin, Recruiter
ON-45, ON-46, ON-77
Must Have
Duplicate Detection & Merge
Admin
ON-57, ON-58
Must Have
Offer Approval Workflow
Admin
ON-43, ON-60
Must Have
Document Signing (NDA)
Admin, Candidate
ON-61, ON-121
Should Have
Settings — Account Profile Edit
All
ON-65, ON-90, ON-123
Should Have
AI Cover Letter Generation
Candidate
ON-110–112
Could Have
Dark Mode
All
ON-66, ON-91, ON-124
Could Have
Accent Color Themes
All
ON-66, ON-92, ON-125
Won’t Have (v1)
Mobile native apps, bulk candidate import, public API v2 docs
—
—

9. Jira Story Index
All 66 user stories are tracked in the OneATS Jira project (key: ON). The tables below map each story to its portal, epic, and ticket number.

9.1  Admin Console Stories (ON-42 – ON-66)
Ticket
Story
Epic
ON-42
US-1.1 — View organization-wide dashboard
E1: Dashboard
ON-43
US-1.2 — Approval Queue for closures and offers
E1: Dashboard
ON-44
US-2.1 — Create a new requisition
E2: Requisitions
ON-45
US-2.2 — Auto-publish new requisitions to Ceipal
E2: Requisitions
ON-46
US-2.3 — Auto-syndicate to job boards via Ceipal
E2: Requisitions
ON-47
US-2.4 — See sync and AI-review status pills
E2: Requisitions
ON-48
US-2.5 — Manually re-run AI matching
E2: Requisitions
ON-49
US-3.1 — Auto-score every applicant
E3: AI Matching
ON-50
US-3.2 — Sort matched candidates into tiers
E3: AI Matching
ON-51
US-3.3 — Show matched and missing skills
E3: AI Matching
ON-52
US-3.4 — Source badges for external applicants
E3: AI Matching
ON-53
US-3.5 — Drill into candidate resume from match results
E3: AI Matching
ON-54
US-4.1 — Master candidate list
E4: Candidates
ON-55
US-4.2 — Track all submissions
E4: Candidates
ON-56
US-4.3 — Add a new candidate manually
E4: Candidates
ON-57
US-5.1 — Surface duplicate candidate records
E5: Duplicates
ON-58
US-5.2 — Resolve duplicate flags (merge or dismiss)
E5: Duplicates
ON-59
US-6.1 — View all scheduled interviews
E6: Interviews & Offers
ON-60
US-6.2 — Approve offers before they go out
E6: Interviews & Offers
ON-61
US-6.3 — Track NDA and document signature status
E6: Interviews & Offers
ON-62
US-7.1 — Track confirmed placements
E7: Placements
ON-63
US-8.1 — Centralized documents hub
E8: Documents
ON-64
US-9.1 — Manage internal users and roles
E9: Settings
ON-65
US-9.2 — Manage personal account details
E9: Settings
ON-66
US-9.3 — Accent color themes and dark mode
E9: Settings

9.2  Recruiter Portal Stories (ON-73 – ON-92)
Ticket
Story
Priority
ON-73
US-R01 — View Recruiting Dashboard
Must Have
ON-74
US-R02 — Create Requisition from Dashboard
Should Have
ON-75
US-R03 — View All Job Requisitions
Must Have
ON-76
US-R04 — View Requisition Details
Must Have
ON-77
US-R05 — Create and Submit a New Requisition
Must Have
ON-78
US-R06 — Save Requisition as Draft
Must Have
ON-79
US-R07 — Edit an Existing Requisition
Must Have
ON-80
US-R08 — View My Candidate List
Must Have
ON-81
US-R09 — View Candidate Profile
Must Have
ON-82
US-R10 — View Submission History
Must Have
ON-83
US-R11 — Review External Applicants from Ceipal
Must Have
ON-84
US-R12 — Reject a Candidate
Must Have
ON-85
US-R13 — View Upcoming Interviews
Must Have
ON-86
US-R14 — Schedule an Interview
Must Have
ON-87
US-R15 — Reschedule an Interview
Must Have
ON-88
US-R16 — Capture Interview Feedback
Must Have
ON-89
US-R17 — Track Offer Status for My Candidates
Must Have
ON-90
US-R18 — Update Account Profile
Should Have
ON-91
US-R19 — Toggle Dark Mode
Could Have
ON-92
US-R20 — Choose Accent Colour Theme
Could Have

9.3  Candidate Portal Stories (ON-100 – ON-125)
Ticket
Story
Epic
ON-100
US-1.1 — Personal at-a-glance dashboard
E1: Dashboard
ON-101
US-1.2 — Recent applications summary card
E1: Dashboard
ON-102
US-1.3 — Upcoming interviews card
E1: Dashboard
ON-103
US-1.4 — Profile and resume dashboard cards
E1: Dashboard
ON-104
US-1.5 — Pending offers action card
E1: Dashboard
ON-105
US-2.1 — View and edit profile information
E2: Profile & Resume
ON-106
US-2.2 — Profile strength indicator
E2: Profile & Resume
ON-107
US-2.3 — Add skills to profile
E2: Profile & Resume
ON-108
US-2.4 — Upload or replace resume
E2: Profile & Resume
ON-109
US-2.5 — AI analysis results on resume card
E2: Profile & Resume
ON-110
US-3.1 — Review AI-drafted cover letter
E3: Cover Letters
ON-111
US-3.2 — Send application with cover letter in one step
E3: Cover Letters
ON-112
US-3.3 — Auto-draft cover letters above match threshold
E3: Cover Letters
ON-113
US-4.1 — View full application history
E4: Applications
ON-114
US-4.2 — Real-time application status updates
E4: Applications
ON-115
US-4.3 — View job description from application row
E4: Applications
ON-116
US-5.1 — View all scheduled interviews
E5: Interviews
ON-117
US-5.2 — Request interview reschedule
E5: Interviews
ON-118
US-6.1 — Review full offer details
E6: Offers & Docs
ON-119
US-6.2 — Accept offer in one click
E6: Offers & Docs
ON-120
US-6.3 — Decline offer
E6: Offers & Docs
ON-121
US-6.4 — Sign documents in-portal
E6: Offers & Docs
ON-122
US-6.5 — Complete documents list with status
E6: Offers & Docs
ON-123
US-7.1 — Update personal information from Settings
E7: Settings
ON-124
US-7.2 — Toggle dark mode
E7: Settings
ON-125
US-7.3 — Choose accent color theme
E7: Settings

10. Open Questions & Decisions Needed
#
Question
Implication
1
What AI model or scoring service powers matching? Internal ML model or third-party API (e.g., OpenAI)?
Affects latency SLA, cost model, and on-premise vs. cloud deployment decisions.
2
Will Ceipal support a webhook for inbound applicant sync, or must OneATS poll on a schedule?
Polling introduces latency; webhook requires Ceipal API contract changes and affects real-time scoring SLA.
3
What is the document signing solution for NDAs — custom implementation or third-party e-signature provider (DocuSign, HelloSign)?
A third-party provider adds cost but reduces compliance risk and development effort significantly.
4
Who owns the recruiter-to-candidate notification channel? Does OneATS send email notifications for offer/interview events, or only in-app?
Email sending requires an email service integration and unsubscribe / deliverability management.
5
What is the data retention policy for rejected/closed candidate records? Fully deleted or archived?
Affects GDPR/CCPA compliance posture and duplicate detection behavior over time.
6
Are tier thresholds (90/75) and the cover-letter trigger (80%) fixed for v1 or configurable per org?
Configurable thresholds require a settings UI and admin controls; fixed values are simpler for v1.
7
Does OneATS need multi-tenancy from day one, or is it a single-tenant deployment for Interonit only?
Multi-tenancy significantly increases architecture complexity; single-tenant is safer for v1.

11. Appendix
11.1  AI Match Tier Reference
Tier
Score Range
Label & Behavior
Tier 1
90 – 100%
Excellent Match — Surface first; auto-draft cover letter if candidate portal enabled
Tier 2
75 – 89%
Strong Match — Surface second; auto-draft cover letter if ≥80%
Tier 3
< 75%
Possible Match — Surface last; no auto-draft

11.2  Accent Color Palette
Theme
Applies To
UI Surfaces Affected
Gold
All portals
Navigation active states, primary buttons, pill borders, profile ring, AI badges, tab underlines
Steel Blue
All portals
Navigation active states, primary buttons, pill borders, profile ring, AI badges, tab underlines
Sage
All portals
Navigation active states, primary buttons, pill borders, profile ring, AI badges, tab underlines
Terracotta
All portals
Navigation active states, primary buttons, pill borders, profile ring, AI badges, tab underlines
Plum
All portals
Navigation active states, primary buttons, pill borders, profile ring, AI badges, tab underlines

11.3  Offer Status Vocabulary
Status Value
Color (Light Mode)
Visible In
Not Yet Offered
Gray
Recruiter Portal
Awaiting Candidate Response
Amber
Recruiter Portal, Admin Console
Accepted
Green
All portals
Declined
Red
All portals
Rescinded
Dark Red
Recruiter Portal, Admin Console

11.4  Source Badges (External Applicants)
Badge Label
Meaning
Internal
Candidate added manually or submitted internally by a recruiter
Ceipal Direct
Applicant submitted directly through Ceipal (not via a syndicated board)
Dice
Applicant sourced from Dice via Ceipal syndication
ZipRecruiter
Applicant sourced from ZipRecruiter via Ceipal syndication
Indeed
Applicant sourced from Indeed via Ceipal syndication


—  End of Document  —
OneHR — Security Requirements Summary
Document type: Security requirements (reverse-engineered from code)
Scope: OneHR-API-Backend (FastAPI/Python) + OneHR-UI
Date: 2026-06-19
Format: Each requirement is summarized as what it is, what it uses (mechanism/technology), and how it's used (where/how it's wired in). Reflects the actual repository state, including known gaps from docs/ARCHITECTURE.md.

SR-1 — Authentication
What it is: Verify caller identity before any protected operation.
What it uses: Bearer JWTs. Pluggable via AUTH_METHOD env var:
LOCAL (default): custom HS-signed JWT created with python-jose (common/jwt_auth.py), signed by JWT_SECRET/JWT_ALGORITHM from AWS SSM.
KEYCLOAK: tokens issued and validated by Keycloak (common/keycloak.py).
*(Cognito appears as a design "decision" in the arch doc but is not implemented.)*
How it's used: get_current_user() is a FastAPI dependency that pulls the Authorization: Bearer credential, calls verify_jwt_token(), and returns the decoded payload (id, email, role, tenantId, permissions). Any decode/expiry failure raises HTTP 401 with WWW-Authenticate: Bearer. In Keycloak mode the user record is re-hydrated from the DB to attach exact IDs, role, and permissions.
SR-2 — Password Storage & Verification
What it is: Credentials must never be stored or compared in plaintext.
What it uses: common/password.py — PBKDF2-HMAC-SHA256, 100,000 iterations, 16-byte random salt (primary); bcrypt supported for legacy hashes.
How it's used: hash_password() stores base64(salt + hash). verify_password() auto-detects format: bcrypt ($2a$/$2b$ prefix) vs PBKDF2, and verifies accordingly. Login (login/login_main.py) calls verify_password() in LOCAL mode before issuing a JWT.
SR-3 — Role-Based Access Control (RBAC)
What it is: Restrict each operation to users holding the required permission.
What it uses: Permission keys stored as a JSON map in the roles table; require_permission() / require_any_permission() dependency factories (common/jwt_auth.py); human-readable policy names mapped to keys in common/policies.py (e.g. employees.create → create_employees). A wildcard all permission grants everything.
How it's used: Routes declare Depends(require_permission("...")); has_perm() checks the JWT's permissions dict (or all) and raises HTTP 403 on failure. Known gap (IB-2): ~92% of routes do not yet apply a policy check — RBAC is implemented but not uniformly enforced.
SR-4 — Multi-Tenant Data Isolation
What it is: A tenant must never read or write another tenant's data.
What it uses: tenantId claim in the JWT; centralized helpers in common/tenancy.py (get_required_tenant_id, assert_same_tenant, tenant_where_clause).
How it's used: Handlers resolve the caller's tenant (401 if missing), scope every query with tenant_id = %s, and compare a fetched row's tenant against the caller (403 on mismatch). Known gap (IB-3): ~289 endpoints still call `get_tenant_id()` directly without the guarded helper, so isolation is not yet uniform.
SR-5 — Tenantless-Query Governance (Release Gate)
What it is: Prevent accidental cross-tenant data exposure from queries that omit a tenant filter.
What it uses: common/governance.py — a governance checker plus decorators that tag repository methods with a category, policy id, and audit-required flag.
How it's used: Any SQL that runs before tenant resolution (platform-global catalogs, trusted webhook/system identity) must be explicitly registered. Unregistered tenantless SQL is treated as a release blocker.
SR-6 — Sensitive Field Encryption
What it is: Encrypt sensitive values at rest beyond DB-level protection.
What it uses: common/encrypt.py — Fernet symmetric encryption (cryptography), key ENCRYPTION_KEY fetched from AWS SSM.
How it's used: encrypt_value() / decrypt_value() wrap plaintext for storage and reverse it on read; empty values pass through as None.
SR-7 — Transport Security to the Database
What it is: Encrypt the application-to-database connection.
What it uses: common/db.py with a configurable DB_SSL_MODE: disable (local only), require (encrypted, no cert validation), verify-full (encrypted + hostname/cert validation). CA bundle via DB_SSL_ROOT_CERT/DB_SSL_CA_FILE.
How it's used: Managed/production runtimes default to verify-full; SSL is forced on the pg8000 connection (a plain non-SSL local Postgres is rejected).
SR-8 — Secrets Management
What it is: Keep secrets (JWT signing key, DB credentials, encryption key, API keys) out of code and source control.
What it uses: AWS SSM Parameter Store + Secrets Manager, accessed through the ParameterStore singleton (common/parameters.py), which batch-fetches all /{APP_CODE}/ parameters WithDecryption=True. .gitignore excludes .env/.env.env.
How it's used: getParam() resolves config with .env overriding, falling back to SSM. JWT secret, encryption key, DB password, Stripe/Zoho/Bedrock credentials are all sourced this way — never hardcoded.
SR-9 — CORS Policy
What it is: Restrict which browser origins may call the API with credentials.
What it uses: Centralized common/cors.py (get_cors_kwargs), driven by ALLOWED_ORIGINS/FRONTEND_URL.
How it's used: Applied as CORSMiddleware on the gateway and sub-apps. In production-like environments (prod/staging) a missing or * origin list raises at startup (refuses to boot with a wildcard); local dev falls back to localhost origins. allow_credentials=True, exposes X-Correlation-ID. Known gap (IB-1): 16/22 modules still apply wildcard CORS.
SR-10 — Webhook Authentication (ATS)
What it is: Authenticate inbound third-party webhooks without a user session.
What it uses: Per-provider HMAC signature validation (provider.validate_webhook(headers, body)) plus a required X-Tenant-ID header (ats/webhooks/receiver.py).
How it's used: POST /ATS/webhooks/{provider_name} looks up the registered provider, verifies the signature (HTTP 401 on failure), requires the tenant header (HTTP 400 if absent), records an audit event, then upserts the candidate. No JWT is used — the provider's HMAC is the trust anchor. *(Framework only; no concrete provider is registered yet.)*
SR-11 — Audit Logging
What it is: Maintain a tamper-evident record of who did what.
What it uses: common/audit.py (record_audit_event), an indexed audit-log table (migrations 0002, 0007).
How it's used: Sensitive actions (status transitions, webhook events, staff/employee/client changes) write an audit row with action, tenant, actor (performed_by), and details. Per-entity audit timelines are exposed (e.g. /EMP/employees/{id}/audit-timeline, /IS/staff/{id}/audit).
SR-12 — Request Traceability
What it is: Correlate logs across a request for forensics and incident response.
What it uses: Correlation-ID middleware in app.py + structured JSON logging (common/logging_utils.py).
How it's used: Every request gets/propagates an X-Correlation-ID (echoed in the response), and start/completion are logged with method, path, status, and duration in JSON.
SR-13 — Digital Signature Integrity (e-Sign)
What it is: Legally bind documents (offer letters, NDAs) via a trusted e-signature provider.
What it uses: common/sign_util.py dispatcher — Zoho Sign (default) or DocuSign (enterprise), selected by SIGN_PROVIDER; credentials from SSM.
How it's used: Modules call get_sign_provider(tenant_id).send_for_signature(...) and poll get_status() (pending/signed/declined/expired); signing status is tracked per employee/closure.
SR-14 — Content Compliance Screening (AI Guardrail)
What it is: Block discriminatory / non-compliant language (EEOC, visa-status, bench-sales) in outbound content.
What it uses: common/bedrock.py — two-stage check: regex pre-filter → AWS Bedrock Claude classification; Claude Vision OCR for image scanning.
How it's used: The email agent's /EA/compliance/check and AI generation path screen text and images before send; flagged content is surfaced to the user.
SR-15 — Workflow State Integrity
What it is: Prevent illegal/forged state changes (e.g. skipping an approval).
What it uses: Centralized finite-state machines in common/workflows.py (validate_transition, record_transition).
How it's used: Handlers validate a requested status change against the entity's allowed transition map before persisting, then record the transition with audit/notification hooks — enforcing, e.g., placement-closure RM→HR approval order and the employee onboarding lifecycle.
SR-16 — Usage / Entitlement Enforcement
What it is: Prevent tenants from exceeding their paid plan limits.
What it uses: common/usage_tracking.py (check_and_record_usage) over tenant_feature_usage, plan limits, and Stripe metered billing.
How it's used: Resource-creating endpoints (employees, projects, invoices, admin users, storage) atomically increment per-feature counters; overages are reported to Stripe and surfaced via /SUB/usage/preflight so the UI can warn before exceeding limits.

Known Security Gaps (tracked in `docs/ARCHITECTURE.md`)
ID
Gap
Affected requirement
Status
IB-1
CORS wildcards in 16/22 modules
SR-9
Pending
IB-2
~92% of routes missing policy check
SR-3
Pending
IB-3
~289 unguarded get_tenant_id() calls
SR-4
Pending
IB-5
No DB connection pooling (resource exhaustion risk)
SR-7 (availability)
Pending

Self-assessed security score: 3/10 → target 8/10 (Day 90). The security *primitives* exist as shared modules; the gap is uniform adoption across all ~22 modules and ~400+ routes.

*Reverse-engineered from the OneHR codebase. "Known gap" items reflect the project's own governance docs and in-code comments as of the date above.*
OneATS
Recruiter Portal — User Stories
Product Owner Specification  ·  OneATS v1 Prototype
Date: June 23, 2026  ·  Role: Recruiter (e.g. Jordan Patel, Interonit Solutions)
Priority Legend
Priority
Definition
Must Have
Core functionality required for the first release.
Should Have
High-value features that are not strictly critical but should be in scope if feasible.
Could Have
Nice-to-have quality-of-life improvements; deferred if time is short.
Won't Have
Out of scope for this iteration; captured for future planning.

E1  Dashboard

US-R01  View My Recruiting Dashboard   [Must Have  |  5 pts]
As a
Recruiter
I want to
see a personalised dashboard on login showing my key metrics, active requisitions, recent submissions, and upcoming interviews
So that
I can understand my pipeline at a glance without navigating to multiple screens
Acceptance
Criteria
	•	Dashboard displays four stat cards: Assigned Requisitions, Submitted This Month, Interviews This Week, and Submission → Offer Rate.
	•	Each stat card shows a value and a trend indicator (e.g. '+1 this week', 'Above team avg').
	•	My Requisitions table lists up to 5 open/draft reqs with Title, Client, Openings, and Status.
	•	Recent Submissions table shows the 5 most recent submissions with Candidate, Role, Submitted date, and Status.
	•	Upcoming Interviews widget shows the next 3 interviews with date chip, candidate name, round, and time.
	•	Pipeline Snapshot funnel shows counts for Sourced → Submitted → Interviewing → Offer → Placed.
	•	All dashboard widgets link to their respective full pages.
Jira Issue
ON-73  —  https://interonit-pmp.atlassian.net/browse/ON-73

US-R02  Create a New Requisition from Dashboard   [Should Have  |  2 pts]
As a
Recruiter
I want to
click '+ New Requisition' from the dashboard header
So that
I can start a req without switching pages first
Acceptance
Criteria
	•	A '+ New Requisition' button is visible in the dashboard hero section.
	•	Clicking it opens the New Requisition modal (same modal as on the Requisitions page).
	•	After submitting or saving as draft, the dashboard refreshes to reflect the new req in My Requisitions.
Jira Issue
ON-74  —  https://interonit-pmp.atlassian.net/browse/ON-74


E2  Job Requisitions

US-R03  View All Job Requisitions   [Must Have  |  3 pts]
As a
Recruiter
I want to
see a full list of job requisitions — including those owned by other recruiters — in a searchable table
So that
I can find any req quickly and understand the firm's overall hiring activity
Acceptance
Criteria
	•	Requisitions page shows a table with columns: Req ID, Title, Client, Recruiter (with 'You' tag for my own), Rate Card, Openings, Status.
	•	Status values include Open, Draft, and Closed.
	•	Open reqs that are synced to Ceipal display a 'Ceipal Synced' badge.
	•	A search box filters the list in real time by title, client, or req ID.
	•	A filter icon allows filtering by status (Open / Draft / Closed).
	•	A refresh icon re-fetches the list from the backend.
Jira Issue
ON-75  —  https://interonit-pmp.atlassian.net/browse/ON-75

US-R04  View Requisition Details   [Must Have  |  3 pts]
As a
Recruiter
I want to
click 'View' on any requisition to open a detail panel
So that
I can review the full job description, rate card, required skills, and external applicant activity without leaving the list
Acceptance
Criteria
	•	Clicking 'View' opens a modal with: req ID, title, client, recruiter, rate card, number of openings.
	•	Full job description text is displayed.
	•	Required skills are displayed as tags.
	•	If external applicants have been auto-matched to this req, a banner shows the count, their names, match scores, and source boards (e.g. Ceipal · Dice).
	•	A 'Close' button dismisses the modal.
Jira Issue
ON-76  —  https://interonit-pmp.atlassian.net/browse/ON-76

US-R05  Create and Submit a New Requisition   [Must Have  |  8 pts]
As a
Recruiter
I want to
fill in a New Requisition form and click Submit
So that
the req is immediately posted to Ceipal and syndicated to Dice, ZipRecruiter, and Indeed automatically
Acceptance
Criteria
	•	New Requisition form captures: Title, Client, Openings, Start Date, Rate Card (Min and Max), Job Description, Required Skills.
	•	All required fields are validated before submission — the form cannot be submitted with empty mandatory fields.
	•	On Submit, an AI/sync toast ('Posting to Ceipal…' → 'Synced to Ceipal — distributing to Dice, ZipRecruiter, Indeed…') confirms the broadcast.
	•	The new req appears in the Requisitions list with status 'Open' and a 'Ceipal Synced' badge.
	•	The req is assigned a unique Req ID (e.g. JR-XXXX).
Jira Issue
ON-77  —  https://interonit-pmp.atlassian.net/browse/ON-77

US-R06  Save a Requisition as Draft   [Must Have  |  3 pts]
As a
Recruiter
I want to
save a partially completed requisition as a draft
So that
I can continue editing it later before publishing to job boards
Acceptance
Criteria
	•	'Save as Draft' button is available alongside 'Submit' in the New Requisition modal.
	•	Draft reqs are saved with status 'Draft' and appear in the Requisitions list.
	•	Draft reqs do not carry a 'Ceipal Synced' badge and are not posted to any external boards.
	•	A draft can be reopened for editing at any time.
Jira Issue
ON-78  —  https://interonit-pmp.atlassian.net/browse/ON-78

US-R07  Edit an Existing Requisition   [Must Have  |  5 pts]
As a
Recruiter
I want to
click 'Edit' on a requisition I own to update its details
So that
I can correct or refine the req without creating a duplicate
Acceptance
Criteria
	•	'Edit' button is only shown for requisitions owned by the logged-in recruiter.
	•	Edit modal pre-populates with the req's current values (title, openings, rate card min/max, description, required skills).
	•	Changes are saved on 'Save Changes'; the list row updates immediately.
	•	Editing a live (Open) req does not reset its Ceipal sync status.
	•	Cancel discards all changes.
Jira Issue
ON-79  —  https://interonit-pmp.atlassian.net/browse/ON-79


E3  Candidates & Submissions

US-R08  View My Candidate List   [Must Have  |  3 pts]
As a
Recruiter
I want to
see a list of all candidates I own, with their target role, key skills, and current status
So that
I can quickly assess who needs action without opening each profile
Acceptance
Criteria
	•	My Candidates tab shows: Candidate name (linked to profile), Target Role, Skills (as tags), Status pill.
	•	Status values: Sourced, Submitted, Interviewing, Offer Extended, Placed, Rejected.
	•	A search box filters candidates by name or role.
	•	Clicking a candidate's name opens their full profile modal.
Jira Issue
ON-80  —  https://interonit-pmp.atlassian.net/browse/ON-80

US-R09  View Candidate Profile   [Must Have  |  5 pts]
As a
Recruiter
I want to
open a candidate's profile to see their contact details, resume, and cover letter
So that
I can evaluate them in full context and take the next action (schedule interview or reject) without leaving the screen
Acceptance
Criteria
	•	Profile modal shows: name, target role, email, phone, location.
	•	Resume is displayed as a downloadable file card (filename, upload date, size).
	•	Cover letter is rendered as formatted text.
	•	For external applicants, an AI banner shows the match percentage, matched req, and tier (e.g. 'Tier 1 — Excellent Match').
	•	For external applicants, the source board (e.g. 'Ceipal · Dice') is shown as a badge.
	•	Action buttons are available: 'Schedule Interview' and 'Reject'.
Jira Issue
ON-81  —  https://interonit-pmp.atlassian.net/browse/ON-81

US-R10  View My Submission History   [Must Have  |  3 pts]
As a
Recruiter
I want to
view a chronological log of all submissions I have made
So that
I can track outcome trends and identify candidates I may re-engage
Acceptance
Criteria
	•	My Submission History tab shows: Candidate, Job, Client, Submitted date, Status.
	•	All historical statuses are shown, including 'Not Selected'.
	•	Rows are sorted by most recent submission first.
	•	Candidate names are clickable links to their profile modal.
	•	A search box filters the history by candidate name or job title.
Jira Issue
ON-82  —  https://interonit-pmp.atlassian.net/browse/ON-82

US-R11  Review External Applicants from Ceipal   [Must Have  |  8 pts]
As a
Recruiter
I want to
see a list of applicants who applied through Ceipal-connected job boards, along with their AI match score and tier
So that
I can quickly triage inbound applicants and focus attention on the strongest matches first
Acceptance
Criteria
	•	External Applicants tab shows: Candidate, Applied Via (board badge), Matched Req, AI Match %, Tier.
	•	AI match score is computed automatically on applicant arrival — no manual trigger required.
	•	Tier labels: Tier 1 — Excellent (≥90%), Tier 2 — Strong (75–89%), Tier 3 — Possible (<75%).
	•	A banner at the top explains the auto-match behaviour.
	•	Clicking 'View' or the candidate name opens their profile with the AI banner pre-populated.
	•	Applicants can be rejected directly from the profile; the row updates to 'Rejected'.
Jira Issue
ON-83  —  https://interonit-pmp.atlassian.net/browse/ON-83

US-R12  Reject a Candidate   [Must Have  |  2 pts]
As a
Recruiter
I want to
reject a candidate from their profile modal
So that
their status is updated immediately and they are removed from my active pipeline
Acceptance
Criteria
	•	A 'Reject' button (styled in red) is available in the candidate profile modal footer.
	•	Clicking 'Reject' closes the modal and updates the candidate's status pill to 'Rejected' in the list.
	•	The rejection action does not require additional confirmation for speed; it is reversible by an admin.
Jira Issue
ON-84  —  https://interonit-pmp.atlassian.net/browse/ON-84


E4  Interviews

US-R13  View Upcoming Interviews   [Must Have  |  3 pts]
As a
Recruiter
I want to
see all interviews I have scheduled, sorted by date
So that
I can prepare in advance and avoid scheduling conflicts
Acceptance
Criteria
	•	Upcoming tab shows a table with: Candidate, Job, Client, Date / Time, Round, Interview Type (Video / Onsite / Phone).
	•	Interviews are sorted by date ascending.
	•	Each row has a 'Reschedule' action.
	•	Interview type is shown as a colour-coded pill (blue = Video, green = Onsite).
Jira Issue
ON-85  —  https://interonit-pmp.atlassian.net/browse/ON-85

US-R14  Schedule an Interview   [Must Have  |  5 pts]
As a
Recruiter
I want to
schedule an interview for a candidate — either from the Interviews page or directly from a candidate's profile
So that
the interview is recorded in one place and visible on my calendar widget
Acceptance
Criteria
	•	'+ Schedule Interview' button is available on the Interviews page header.
	•	'Schedule Interview' button in the candidate profile modal pre-fills the candidate name and role.
	•	Schedule Interview modal captures: Date, Time, Round (e.g. Round 1), Interview Type (Video / Onsite / Phone).
	•	All fields are required before the interview can be saved.
	•	On save, the new interview appears in the Upcoming tab with correct details.
	•	The dashboard Upcoming Interviews widget also reflects the new entry.
Jira Issue
ON-86  —  https://interonit-pmp.atlassian.net/browse/ON-86

US-R15  Reschedule an Interview   [Must Have  |  3 pts]
As a
Recruiter
I want to
reschedule an upcoming interview
So that
the updated time is reflected system-wide without creating a duplicate entry
Acceptance
Criteria
	•	Each row in the Upcoming tab has a 'Reschedule' button.
	•	Clicking it opens the Schedule Interview modal pre-filled with the existing date, time, and type.
	•	Saving updates the existing row in place — no duplicate is created.
	•	The dashboard Upcoming Interviews widget reflects the updated time.
Jira Issue
ON-87  —  https://interonit-pmp.atlassian.net/browse/ON-87

US-R16  Capture Interview Feedback   [Must Have  |  5 pts]
As a
Recruiter
I want to
record structured feedback for a completed interview round
So that
hiring managers and the admin team can review my assessment without a separate conversation
Acceptance
Criteria
	•	Feedback Pending tab lists completed interviews that still need feedback, showing Candidate, Job, Round, and Interview Date.
	•	Clicking 'Capture Feedback' opens a modal pre-labelled with the candidate and round.
	•	Feedback form captures: Overall Rating (1–5 stars), Recommendation (Strong No / No / Yes / Strong Yes), Strengths (text area), Concerns (text area).
	•	Feedback can be saved as a draft or submitted.
	•	On submission, the row is removed from the Feedback Pending list.
	•	Submitted feedback is visible to the admin team.
Jira Issue
ON-88  —  https://interonit-pmp.atlassian.net/browse/ON-88


E5  Offers

US-R17  Track Offer Status for My Candidates   [Must Have  |  3 pts]
As a
Recruiter
I want to
see the current offer status for every candidate I have placed in an offer stage
So that
I can follow up promptly and inform the client of the outcome
Acceptance
Criteria
	•	Offers page shows a table with: Candidate, Job, Client, Status.
	•	Status values: Not Yet Offered, Awaiting Candidate Response, Accepted, Declined, Rescinded.
	•	Status is shown as a colour-coded pill matching platform conventions.
	•	The table is scoped to the logged-in recruiter's candidates only.
Jira Issue
ON-89  —  https://interonit-pmp.atlassian.net/browse/ON-89


E6  Settings & Personalisation

US-R18  Update My Account Profile   [Should Have  |  2 pts]
As a
Recruiter
I want to
edit my account details — name, email, title, organisation, phone, and location
So that
my profile is accurate and contacts can reach me through the correct channels
Acceptance
Criteria
	•	Settings page displays editable fields: Full Name, Email, Title, Organisation, Phone, Location.
	•	Fields are pre-populated with the current saved values.
	•	'Save Changes' persists all updates; a success message is shown.
	•	'Reset' reverts all fields to their last saved state without reloading the page.
	•	Email field validates format before saving.
Jira Issue
ON-90  —  https://interonit-pmp.atlassian.net/browse/ON-90

US-R19  Toggle Dark Mode   [Could Have  |  2 pts]
As a
Recruiter
I want to
switch the interface between light and dark mode
So that
I can work comfortably in different lighting environments
Acceptance
Criteria
	•	A 'Dark Mode' toggle is available in Settings > Appearance.
	•	Enabling dark mode applies a dark background and adjusts text, border, and status colours system-wide.
	•	The preference is persisted in the browser and restored on next login.
	•	All pages and modals respect the dark mode setting consistently.
Jira Issue
ON-91  —  https://interonit-pmp.atlassian.net/browse/ON-91

US-R20  Choose an Accent Colour Theme   [Could Have  |  2 pts]
As a
Recruiter
I want to
select an accent colour from a curated palette (Gold, Steel Blue, Sage, Terracotta, Plum)
So that
I can personalise my workspace to my taste while staying within the approved brand palette
Acceptance
Criteria
	•	Settings > Appearance shows five accent colour cards, each with a preview swatch, name, and description.
	•	Clicking a card immediately applies the accent colour to all active and accent UI elements (buttons, active nav, pill borders, etc.).
	•	The selected card is highlighted with a border matching the chosen accent.
	•	The preference persists in the browser across sessions.
	•	Dark mode and accent colour operate independently — any combination is valid.
Jira Issue
ON-92  —  https://interonit-pmp.atlassian.net/browse/ON-92

