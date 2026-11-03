# Run Loom on your computer

This private repository requires access granted by its owner. Clone it or download and extract its ZIP.

## Docker: Windows, macOS, or Linux

### Windows PC launcher

Install and start Docker Desktop using Linux containers. Open PowerShell in the extracted repository and run `powershell -NoProfile -File .\Start-Loom.ps1`. Your local PowerShell policy must permit the script. The launcher creates `.env` only when absent, builds and starts the container, checks health, and opens your browser.

### Mac launcher

Install and start Docker Desktop for your Mac's processor (Apple silicon or Intel). In Terminal, run `bash Start-Loom.command` from the repository folder. A Git clone also supports double-clicking the executable `Start-Loom.command`; ZIP downloads may need executable permission restored. The launcher creates a private `.env` only when absent and opens the browser after the service is healthy.

Both launchers use the same Docker package and preserve the existing key and Docker volume. They are launch scripts, not native compiled applications. End-to-end Windows and Mac launcher validation remains pending on hosts with Docker Desktop.

Install and start Docker Desktop, or Docker Engine with Compose on Linux. Use Linux containers on Windows.

Create a local `.env` file in the repository folder containing `LOOM_API_KEY=` followed by a randomly generated secret of at least 32 characters. A password manager can generate it. This file is excluded from Git and the container build.

Run from the repository folder:

```sh
docker compose up --build -d
```

Open http://127.0.0.1:10000 and enter your key. Select a synthetic example and click Run analysis. Core analysis requires no paid API or model download.

The named Docker volume retains graphs and jobs across container replacement. The port is accessible from this computer only. `docker compose down` stops the service while retaining data. Preserve the volume when upgrading. After updating the source, run the build command again.

Inspect startup with `docker compose ps` and `docker compose logs --tail 50 loom`.

## Native: macOS or Linux

Use Python 3.9+ with SQLite FTS5 support; deployment targets Python 3.13. Follow the environment configuration and start commands in the main README. The worker uses POSIX resource controls, so Windows users should use Docker.

## Optional capabilities

- PDF and Excel reading: included in the Docker image. For native installation, run `python -m pip install -r requirements-intake.txt`.
- Semantic retrieval: install `requirements-semantic.txt` and supply local model directories according to `runtime/RESEARCH_STUDIO.md`. The package install alone does not provide model files.
- Accelerated simulation: optional NumPy.

Docker execution has not been verified on the authoring PC, where Docker is unavailable. Confirm successful startup and a synthetic example on your target computer.
