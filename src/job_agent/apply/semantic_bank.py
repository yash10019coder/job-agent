"""Labeled example pages for the semantic classifier (semantic.py). Each example is a page DESCRIPTION in the same shape
`semantic.describe` produces: Title | Headings | Notice | Text | Fields | Buttons. Add real misclassified pages here
(descriptions only, never personal values) and the classifier improves without any new rules.
"""
from __future__ import annotations

BANK: dict[str, list[str]] = {
    "dead": [
        "Title: 404 Page not found | Headings: Page not found | Text: The page you are looking for does not exist or has been moved. | Buttons: Go to homepage",
        "Title: Oops! | Headings: Something went wrong | Text: We could not find that job. The link may be broken or the posting was removed. | Buttons: Back to jobs",
        "Title: Error | Headings: 410 Gone | Text: This resource is no longer available. | Buttons: Home",
        "Title: Job not found | Headings: We can't find this job | Text: Sorry, that opening doesn't exist anymore. Try searching for another role. | Buttons: Search jobs",
        "Title: Application unavailable | Notice: The requested job could not be found. | Buttons: Return to careers site",
    ],
    "closed": [
        "Title: Senior Engineer | Headings: Senior Engineer | Text: This position is no longer accepting applications. | Buttons: View other openings",
        "Title: Job closed | Headings: This job has expired | Text: Sorry, the job you are looking for has been filled or closed. | Buttons: See similar jobs",
        "Title: Product Designer | Notice: This job is no longer available. | Text: Thank you for your interest. The role has been filled. | Buttons: Browse open roles",
        "Title: Careers | Headings: Position filled | Text: We have stopped accepting applications for this opening. Check back later for new roles. | Buttons: All jobs",
        "Title: Data Analyst | Headings: Data Analyst | Text: Applications for this position are closed. The deadline has passed. | Buttons: Back to job search",
        "Title: Opening unavailable | Notice: This requisition is no longer open. | Buttons: Search for jobs",
    ],
    "listing": [
        "Title: Careers | Headings: Open positions; 48 jobs | Text: Search jobs by keyword, department and location. | Fields: text Search; select Department; select Location | Buttons: Search; Reset filters",
        "Title: Jobs at Acme | Headings: Current openings | Text: Showing 25 of 112 results. Sort by newest. | Fields: text Keywords; select Team; select Office | Buttons: Search; Load more",
        "Title: Join our team | Headings: All jobs | Text: Engineering (14) Design (6) Sales (9) Marketing (4) | Fields: select Job category; select Employment type | Buttons: Apply filters",
        "Title: Job search | Headings: Search results | Text: 37 jobs found. Software Engineer, Remote. Data Scientist, London. Product Manager, New York. | Fields: text Search jobs | Buttons: Search",
        "Title: Opportunities | Headings: Explore roles | Text: Find your next role. Filter by location and function. | Fields: text Search; select Location | Buttons: Search",
    ],
    "bot_block": [
        "Title: Just a moment... | Headings: Checking if the site connection is secure | Text: Verifying you are human. This may take a few seconds. Enable JavaScript and cookies to continue.",
        "Title: Access denied | Headings: Access Denied | Text: You don't have permission to access this page. Reference number. Your request looks automated.",
        "Title: Attention Required | Headings: Sorry, you have been blocked | Text: This website is using a security service to protect itself from online attacks.",
        "Title: Pardon our interruption | Text: As you were browsing something about your browser made us think you were a bot. Press and hold to confirm you are a human.",
        "Title: 403 Forbidden | Headings: Forbidden | Text: Unusual traffic detected from your network. Please try again later.",
    ],
    "job_page": [
        "Title: Backend Engineer | Headings: Backend Engineer; About the role; Responsibilities; Requirements | Text: We are looking for an engineer to build services. 3+ years of Python. Benefits include remote work. | Buttons: Apply; Share",
        "Title: Senior Product Manager - Acme | Headings: Job description; What you will do; Qualifications | Text: Lead product strategy. Salary range. Location: Remote. | Buttons: Apply now; Save job",
        "Title: Data Scientist | Headings: Data Scientist; Overview; Responsibilities; Minimum qualifications | Text: Posted 3 days ago. Full time. Hybrid. | Buttons: APPLY NOW; Copy link; Share on LinkedIn",
        "Title: Platform Software Engineer 2 | Headings: Job Description; Responsibilities; Required Skills | Text: Design and build cloud services. Equal opportunity employer. | Buttons: APPLY NOW; View More Jobs; Add Job to My Job Selection",
        "Title: Customer Success Manager | Headings: About us; The role; What we offer | Text: Join our growing team. Competitive salary and benefits. | Buttons: Apply for this job",
        "Title: Machine Learning Engineer | Headings: Job details; Who you are | Text: Experience with PyTorch and large-scale training. | Buttons: Apply; Sign in; Back to all jobs",
    ],
    "form_step": [
        "Title: Apply - Software Engineer | Headings: Application; Personal information | Fields: text First name; text Last name; email Email; tel Phone; file Resume | Buttons: Next; Back",
        "Title: Job application | Headings: Contact details | Fields: text Full name; email Email address; tel Mobile number; text Current location; text LinkedIn URL | Buttons: Continue",
        "Title: Application form | Headings: Application: Member of Technical Staff | Fields: file Resume/CV; text First name *; text Last name *; email Email *; combobox Location *; textarea Why do you want to work here? | Buttons: Submit application",
        "Title: Apply for Data Engineer | Headings: Work experience; Education | Fields: text Current company; text Job title; text Years of experience; select Notice period; radio Are you authorized to work in this country? | Buttons: Save and continue",
        "Title: Additional questions | Headings: Questions | Fields: radio Do you require visa sponsorship?; select How did you hear about us?; textarea Expected salary; checkbox I agree to the privacy policy | Buttons: Next",
        "Title: Equal opportunity | Headings: Voluntary self-identification | Fields: select Gender; select Race; select Veteran status; select Disability status | Buttons: Next; Back",
        "Title: Job application form | Headings: Authentication screen. You don't need to have an account | Fields: email Email Address *; checkbox I agree with the terms and conditions * | Buttons: Back to Job Details; CANCEL; NEXT",
        "Title: Start your application | Headings: Enter your email to begin | Text: We will send you a code to verify your email. No account is needed. | Fields: email Email | Buttons: Continue",
    ],
    "review": [
        "Title: Review your application | Headings: Review and submit | Text: Please review your information before submitting. Name, email, resume, answers. | Buttons: Submit application; Edit; Back",
        "Title: Application summary | Headings: Review | Text: Check your details. You will not be able to change them after submitting. | Buttons: Submit; Go back",
        "Title: Almost done | Headings: Confirm and submit | Text: Review your answers. By submitting you agree to the terms. | Buttons: Send application; Previous",
    ],
    "confirmation": [
        "Title: Application submitted | Headings: Thank you for applying | Text: We have received your application and will be in touch if there is a match.",
        "Title: Thanks! | Headings: Your application has been submitted | Text: A confirmation email has been sent to you. Our recruiting team will review your profile.",
        "Title: Confirmation | Headings: Application received | Text: Thank you for your interest in this role. We will contact you about next steps.",
        "Title: Success | Headings: You've applied! | Text: Your application was sent successfully. Track your application status in your candidate profile. | Buttons: View my applications; Search more jobs",
        "Title: Thank you | Headings: Thank you for your submission | Text: Your application is complete. We appreciate your interest in joining us.",
    ],
    "email_code": [
        "Title: Job application form | Headings: Confirm Your Identity | Notice: The verification code was sent to this email address. When you get the code, type it into the field to confirm your identity. | Fields: text Enter verification code digit 1 of six; text digit 2 of six; text digit 3 of six | Buttons: VERIFY; Send New Code",
        "Title: Verify your email | Headings: Enter the code we sent you | Text: We emailed a 6-digit security code to your address. It expires in 10 minutes. | Fields: text Security code | Buttons: Verify; Resend code",
        "Title: Verification | Headings: Check your inbox | Text: Enter the one-time passcode we just sent to continue. | Fields: text One-time code | Buttons: Submit; Resend",
        "Title: Confirm your email address | Headings: Email verification | Text: We sent a confirmation code to your email. Type it below. | Fields: text Confirmation code | Buttons: Confirm; Didn't get a code?",
        "Title: Two-step verification | Headings: Enter your OTP | Text: A one time password has been sent to your email. | Fields: text OTP | Buttons: Validate; Resend OTP",
    ],
    "login": [
        "Title: Sign in | Headings: Sign in to your account | Fields: email Email; password Password | Buttons: Sign in; Forgot password?",
        "Title: Candidate login | Headings: Returning candidates | Text: Log in to continue your application. | Fields: text Username; password Password | Buttons: Log in; Reset password",
        "Title: Welcome back | Headings: Log in | Text: Enter your credentials to access your candidate profile. | Fields: email Email address; password Password | Buttons: Login",
    ],
    "signup": [
        "Title: Create account | Headings: Create your candidate account | Fields: text First name; text Last name; email Email; password Password; password Confirm password | Buttons: Create account",
        "Title: Register | Headings: New to us? Sign up | Text: Create a profile to apply. Passwords must contain 8 characters. | Fields: email Email; password Password; password Verify password | Buttons: Sign up",
        "Title: Join | Headings: Create an account to apply | Fields: email Email address; password Choose a password; checkbox I agree to the terms | Buttons: Register",
    ],
    "account_choice": [
        "Title: Apply | Headings: How would you like to apply? | Text: Sign in to your account, create a new account, or continue as a guest. | Buttons: Sign in; Create account; Apply as guest",
        "Title: Candidate home | Headings: Returning candidate? New candidate? | Buttons: Sign in; Create an account; Continue without an account",
    ],
    "sso_only": [
        "Title: Apply | Headings: Sign in to apply | Text: Use your professional account to continue. | Buttons: Continue with Google; Continue with LinkedIn",
        "Title: Log in | Headings: Choose how to sign in | Buttons: Sign in with Google; Sign in with Apple; Sign in with Microsoft",
    ],
}
