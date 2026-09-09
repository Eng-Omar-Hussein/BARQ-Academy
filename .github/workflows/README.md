## CI

The project uses GitHub Actions for continuous integration. The workflow is defined in:

```text
.github/workflows/ci.yml
```

The CI pipeline runs automatically on every **push** and **pull request**.

### Pipeline

The workflow performs the following checks in order:

```text
Checkout
   ↓
Gitleaks secret scan
   ↓
Python syntax check
   ↓
Python unit tests
   ↓
Shell syntax checks
   ↓
Docker Compose validation
   ↓
Build Docker images
   ↓
Trivy image security scan
   ↓
Start application stack
   ↓
Wait for /ready
   ↓
validate.py
   ↓
failure_test.py
   ↓
backup.sh
   ↓
restore.sh
   ↓
Failure logs
   ↓
Teardown
```

### Code and Configuration Validation

The pipeline performs static checks before starting the Docker environment:

* Python syntax validation using `py_compile`.
* Unit tests using Python `unittest`.
* Shell syntax validation using `bash -n`.
* Docker Compose configuration validation using `docker compose config -q`.

The application unit tests run independently of Docker using fake dependencies.

### Secret Scanning

**Gitleaks** is used to detect accidentally committed secrets such as:

* API keys
* Passwords
* Access tokens
* Private keys
* Other credential-like values

The secret scan runs immediately after checkout. A detected secret causes the CI job to fail.

Real credentials must never be stored in the repository. The CI environment is initialized from `.env.example`, which must contain only non-sensitive example values.

### Container Image Security

After the Docker images are built, **Trivy** scans the resulting images for known vulnerabilities.

The scan checks:

* Operating-system packages
* Application/library dependencies
* HIGH and CRITICAL vulnerabilities

Unfixed vulnerabilities are ignored using `--ignore-unfixed`.

A detected fixed HIGH or CRITICAL vulnerability causes the CI pipeline to fail.

### Runtime Validation

After the images pass the security scan, Docker Compose starts the complete application stack.

The workflow waits for the `/ready` endpoint using bounded connection and request timeouts. This prevents the CI job from hanging indefinitely if the service fails to become ready.

The main functional validation is then executed with:

```bash
python validate.py --url http://127.0.0.1:8080
```

The failure/recovery behavior is tested with:

```bash
python failure_test.py \
  --url http://127.0.0.1:8080 \
  --target app-02
```

### Backup and Restore

The CI pipeline also verifies the backup and restore scripts:

```bash
bash backup.sh ./backups
bash restore.sh ./backups/*.sql
```

This provides an automated check that the database backup and restoration workflow is executable in the CI environment.

### Failure Diagnostics and Cleanup

If a pipeline step fails, Docker Compose logs are collected automatically:

```bash
docker compose -p barq-assessment logs --no-color
```

The stack is always torn down using:

```bash
docker compose -p barq-assessment down --volumes
```

This ensures that containers, networks, and volumes created during the CI run do not remain on the GitHub Actions runner.

### Security and Supply-Chain Controls

The CI pipeline provides multiple security layers:

| Layer            | Tool                       | Purpose                               |
| ---------------- | -------------------------- | ------------------------------------- |
| Repository       | Gitleaks                   | Detect committed secrets              |
| Python code      | Syntax checks + unit tests | Detect code errors                    |
| Shell scripts    | `bash -n`                  | Detect shell syntax errors            |
| Compose          | Docker Compose validation  | Validate infrastructure configuration |
| Container images | Trivy                      | Detect known vulnerabilities          |
| Runtime          | `validate.py`              | Verify application contracts          |
| Resilience       | `failure_test.py`          | Verify backend failure handling       |
| Data protection  | Backup/restore             | Verify database recovery workflow     |

GitHub Actions dependencies should be pinned to immutable commit SHAs where possible to reduce software supply-chain risk.
