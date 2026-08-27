# LectureSift Backup Policy

LectureSift keeps three recovery layers:

1. `main` retains the full Git history used by Netlify and Render.
2. `backup/latest` is refreshed after every push to `main` and once per day.
3. `backup/stable` points to the latest manually verified production release and is moved only after live checks pass.

The backup workflow also creates complete repository and frontend-only archives. GitHub retains each archive for 90 days.

Before every production release:

- run the automated tests;
- publish to `main`;
- verify the Netlify interface and Render health endpoint;
- move `backup/stable` to the verified commit only after both checks pass.

If a release fails, restore from `backup/stable`, then investigate the failed commit without rewriting or deleting the stable backup.

Initial verified recovery point: LectureSift V4.1 (`0e1c8d29e880c1836866ba8eab33a227e402b64b`).
