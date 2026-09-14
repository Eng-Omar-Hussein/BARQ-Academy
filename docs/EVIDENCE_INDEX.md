# Evidence and submission index

* Repository URL: `https://github.com/Eng-Omar-Hussein/BARQ-Academy.git`
* Final commit: `9c39bf345ed1c86b11b4f212e8dd140788baa71b`
* Matching CI run: `https://github.com/Eng-Omar-Hussein/BARQ-Academy/actions/runs/34791193451/job/103815609916`
* Continuous 12–18 minute video URL: `https://drive.google.com/file/d/1wAqO99To_RL8U7gpEhVdZiSbS0izLskd/view?usp=sharing`
* Challenge receipt ID: `ce64da671fca4802b82842019500925b`
* Starting video commit: `3a697a1fa53495ea3ed3eb32bf08e910f360c871`
* Later documentation-only commits, if any: None. Three post-video commits exist and are
  explained under "Post-Video Configuration Updates" below; they are configuration/CI
  changes, not documentation.

## Requirement Traceability

Each requirement is mapped from the implementation/output to its Git commit and the corresponding video timestamp.

| Requirement                       | File / Output                                | Commit                 | Video Timestamp |
| --------------------------------- | -------------------------------------------- | ---------------------- | --------------- |
| Architecture diagram              | `<diagram file>`                             | `9da06c3e87bc4299d91`  | `00:00–00:26`   |
| Starting repository state         | Git history + clean working tree             | `3a697a1fa53495ea3ed3` | `00:26–00:55`   |
| Environment startup               | `docker-compose.yml` / Compose configuration | `b2db764e9f9942a405b`  | `00:55–01:27`   |
| Required endpoints                | Application / NGINX configuration            | `9b08964b62ec6d23f7a`  | `01:27–02:30`   |
| `/instance` load balancing        | NGINX + application configuration            | `687fd1cf9b16e6dcd47`  | `02:30–03:05`   |
| Record persistence                | PostgreSQL volume / Compose configuration    | `8945395500b06382de9`  | `03:05–05:45`   |
| Backend failure and recovery      | Compose / NGINX configuration                | `15a09ba120c8de6233b`  | `05:45–07:07`   |
| Validation                        | Validation script / configuration            | `da926a037e093a32dd3`  | `07:07–07:57`   |
| Video Challenge                   | `video_challenge.sh`                         | `2b5c37d6388c6cc0146`  | `07:57–10:02`   |
| Public port changed `8080 → 8090` | local `.env` (gitignored, no commit — live env var) | `live-only` | `10:02–12:02`   |
| Third application instance        | `docker-compose.yml` / related configuration | `2b5c37d6388c6cc0146`  | `12:02–13:40`   |
| Three-instance proof              | Runtime output from validation script        | `2b5c37d6388c6cc0146`  | `13:40–15:10`   |
| Historical log finding            | `logs/access.log`, `logs/error.log`          | `5e75b388f08c3c90f6c`  | `15:10–17:15`   |
| Final state shown in video        | Git status / add / commit / push / log       | `2b5c37d6388c6cc0146`  | `17:15–18:30`   |

## Post-Video Configuration Updates

Everything required by the task — the live challenge, the port change, the third instance,
and the validation rerun — was completed and committed on camera by `2b5c37d6388c6cc0146`.
That commit is the final state **demonstrated in the video**.

After recording, I updated `.env.example` to `PUBLIC_PORT=8090` so the *submitted repository's*
default matches the final three-instance/8090 architecture, not just my local machine. 
Doing that alone would have broken CI: `ci.yaml` separately hardcoded
`http://127.0.0.1:8080` as the URL it tests against, so if only `.env.example` changed, CI
would have brought the stack up on 8090 while still curling 8080. To avoid that, I updated
`.env.example` and `ci.yaml`'s hardcoded URL together in the same commit (`eebfc56`) so CI
kept passing. The next two commits then removed the second hardcoded copy entirely, so CI
loads the port from `.env.example` instead of keeping two values in sync by hand:

* `eebfc563659f720bf13` — change the CI port from 8080 to 8090
* `2873ac2a5f66f94a99a` — fix(ci): load application port dynamically from .env.example
* `9c39bf345ed1c86b11b` — fix(ci): update port variable to use PUBLIC_PORT from .env.example

These are real changes to `.github/workflows/ci.yaml` and `.env.example`, not documentation,
so I'm listing them here rather than calling them documentation-only. None of them touch the
application, Compose stack, or NGINX config that was demonstrated live — they only remove a
duplicated value so CI reads the same port the running stack already uses.
