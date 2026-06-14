# Grader image. Runs on the DEDICATED grader host, ideally inside a microVM
# (Firecracker/Kata) or gVisor — see README §4. This image bundles `isolate`
# plus the language toolchains; the microVM/gVisor is the hard security boundary.
FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive

# Base + isolate build deps. (isolate is built from source; pin a release in CI.)
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates git build-essential libcap-dev pkg-config \
        python3 python3-pip \
        # Wave 1 toolchains (add the rest per README §5 rollout):
        gcc g++ \
    && rm -rf /var/lib/apt/lists/*

# Build & install isolate (cgroups v2). Pin a tag in real builds.
# NO `|| true`: isolate is the entire sandbox boundary — a failed build must fail
# the image, not ship a grader that runs untrusted code without a sandbox.
RUN git clone --depth 1 https://github.com/ioi/isolate /opt/isolate \
    && make -C /opt/isolate install \
    && isolate --version

WORKDIR /srv
COPY worker/requirements.txt .
RUN pip3 install --no-cache-dir --break-system-packages -r requirements.txt
COPY worker/ ./worker/
COPY judge/ ./judge/
COPY problems/ ./problems/   # registry.load_problem reads problems/<key>/problem.py at grade time

# NOTE: isolate needs cgroup delegation + privileged-ish setup; configure the
# microVM/gVisor runtime and a non-root service user in deployment, not here.
CMD ["python3", "worker/worker.py"]
