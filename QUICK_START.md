# Start Loom

1. Install and start Docker Desktop. On Windows, use Linux containers.
2. Download and extract this repository, or clone it. Access to this private repository is required.
3. Windows: double-click `Start-Loom.cmd`. Mac: open Terminal in this folder and run `bash Start-Loom.command` (an executable Git checkout also supports double-click).
4. On first start, allow time for the container and PDF/Excel packages to download. An internet connection is needed for the initial build.
5. The launcher creates your local credentials, starts the service, and runs an authenticated synthetic analysis. After it passes, the browser opens with your access key on the clipboard. Paste into Access key, select an example, and run it.

The scripts preserve existing credentials and stored projects. Keep `.env` private. Windows execution policy may require you to allow the downloaded PowerShell script under your normal local policy; the launcher does not bypass that policy.

To stop: run `docker compose down` in this folder. This preserves the data volume. To update: download the updated source into the same project folder while preserving `.env`, then launch again. Keep the folder name stable so Compose reuses the same project volume.

If startup fails, read the launcher message and run `docker compose logs --tail 50 loom`. Successful health checks alone do not verify analysis: the launcher also runs `check_install.py` before opening the app.

This is a Docker-assisted installation, not a standalone native installer. Windows and Mac end-to-end startup still require validation on Docker-equipped machines.
