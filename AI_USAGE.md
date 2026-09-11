# AI usage disclosure

## Use 1

* Tool/model: GitHub Copilot
* Purpose: Assist with writing Git commit messages.
* Files or decisions affected: Commit message wording only.
* What you changed or rejected: Used Copilot suggestions as a starting point and reviewed/adjusted the messages to accurately describe the changes.
* How you independently verified it: Verified each commit's actual changes using Git history and the working tree.
* Related commit: `fix(ci): ensure full repository checkout by setting fetch-depth to 0`.

## Use 2

* Tool/model: Claude
* Purpose: Assist with writing validation/troubleshooting scripts and troubleshooting technical issues during the assessment.
* Files or decisions affected: Assessment scripts and related troubleshooting work.
* What you changed or rejected: Reviewed the generated suggestions, adapted scripts to the assessment requirements and my environment, and rejected suggestions that did not match the actual system behavior or requirements.
* How you independently verified it: Ran the scripts and commands against the assessment environment, checked their actual output, tested the application endpoints and services, and verified the results independently.
* Related commit: `fix(docker): remove unused COPY instruction for app.env to enhance security`.

## Use 3

* Tool/model: ChatGPT
* Purpose: Generate and improve diagrams/images used to document and explain the system architecture and technical decisions.
* Files or decisions affected: Architecture diagrams and related documentation.
* What you changed or rejected: Used generated visuals as documentation aids and reviewed/adjusted them to accurately represent the implemented architecture.
* How you independently verified it: Compared the diagrams with the actual Docker Compose configuration, services, networking, CI/CD workflow, and implemented system behavior.
* Related commit: `feat(architecture): add architecture diagram to enhance project documentation`.

