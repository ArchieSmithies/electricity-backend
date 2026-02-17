FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app
COPY . .

# Non-root user for security
RUN adduser --disabled-password --gecos "" appuser && \
    chown -R appuser:appuser /app
USER appuser

# Railway provides PORT via environment variable
ENV PORT=8000
EXPOSE $PORT

# Use gunicorn with PORT from environment
CMD gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --timeout 60 --access-logfile -
```

**Key changes:**
- Added `ENV PORT=8000` (default if Railway doesn't set it)
- Changed `EXPOSE 8000` to `EXPOSE $PORT`
- Changed CMD to use `$PORT` instead of hardcoded `8000`

5. Commit the changes

Railway will rebuild the Docker image and redeploy.

---

## Alternative: Delete the Dockerfile (easier)

If you want Railway to use the Procfile instead of Docker:

1. Go to `https://github.com/ArchieSmithies/electricity-backend`
2. Click on **`Dockerfile`**
3. Click the **three dots** (⋯) → **"Delete file"**
4. Commit

Railway will then use Nixpacks (their auto-builder) which reads the Procfile.

---

## Also: Check Railway's Port Settings

While it's rebuilding:

1. Railway → **"Variables"** tab
2. Check if there's a `PORT` variable
3. If there is, what value does it have?
4. Railway usually sets this automatically, but sometimes it doesn't

If you don't see a `PORT` variable:
- Click **"New Variable"**
- Name: `PORT`
- Value: `8000`
- Add it

---

## Recommended Approach

**I'd suggest deleting the Dockerfile** (the alternative option above). It's simpler and Railway's Nixpacks handles Python apps really well with just the Procfile.

After deleting the Dockerfile, make sure your `Procfile` says:
```
web: gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --timeout 60
