# Reproduce the published figures from the committed dataset — no network, no
# SSB extras (ROADMAP Phase I). Pinned base image and lockfile so the same
# input gives the same output years from now.
#
#   docker build -t circwood .
#   docker run --rm circwood                      # the test-suite: proves the dataset is sound
#   docker run --rm circwood circwood-bundle      # the site bundle
#   docker run --rm -p 8501:8501 circwood streamlit run app/streamlit_app.py --server.address 0.0.0.0
#
# A fresh SSB refresh needs the ssb extras and network:
#   docker run --rm circwood sh -c "pip install -e .[ssb] && circwood-refresh --report /tmp/r.md"
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Lockfile first so dependency layers cache across source changes.
COPY requirements-lock.txt ./
RUN pip install --no-deps -r requirements-lock.txt

COPY . .
RUN pip install --no-deps -e .

# The container's default job is the proof: the committed dataset passes every fence.
CMD ["pytest", "-q"]
