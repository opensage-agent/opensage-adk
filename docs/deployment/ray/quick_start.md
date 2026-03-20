# AgentDocker-Lite: Ray Cluster Quick Start

## 1. Bring Up Cluster

```bash
# Sync code + create worker VMs
gcloud compute ssh <HEAD_VM> --project=<GCP_PROJECT> --zone=<ZONE> \
  --command="cd ~/opensage && git pull --ff-only && ~/venv/bin/ray up ~/opensage/ray/opensage-eval-cluster.yaml -y --no-config-cache"
```

## 2. Run Evaluation

Run from the **head node** (SSH in first, or use `gcloud compute ssh`).
Must run as root (`sudo`) because agentdocker-lite sandboxes require root for `mount -t overlay`.

```bash
# overlayfs (agentdocker-lite sandbox)
sudo GOOGLE_API_KEY=<YOUR_API_KEY> \
  ~/venv/bin/python -u -m benchmarks.swe_bench_pro.swe_bench_pro generate \
  --config_template_path examples/agents/swebenchpro_agent/agentdocker_lite_overlayfs_config.toml \
  --agent_dir examples/agents/swebenchpro_agent \
  --start_idx 0 --end_idx 150 \
  --max_workers 8 --use_ray \
  --llm_retry_count 10 --llm_retry_timeout 30 \
  --output_dir /data/evals/overlayfs_eval \
  --log_level INFO

# Docker baseline (pre-pull images first — see below)
sudo GOOGLE_API_KEY=<YOUR_API_KEY> \
  ~/venv/bin/python -u -m benchmarks.swe_bench_pro.swe_bench_pro generate \
  --config_template_path src/opensage/evaluations/configs/swe_bench_pro_docker_no_neo4j_config.toml \
  --agent_dir examples/agents/swebenchpro_agent \
  --start_idx 0 --end_idx 150 \
  --max_workers 8 --use_ray \
  --llm_retry_count 10 --llm_retry_timeout 30 \
  --output_dir /data/evals/docker_eval \
  --log_level INFO
```

**Important notes**:

- Use `--llm_retry_count 10` for resilience against transient API errors
- For Docker: pre-pull all images on workers before eval to avoid timeouts:
  ```bash
  python3 ray/prepull_docker_images.py --start 0 --end 150 --parallel 4
  ```
- For long runs, wrap with `nohup ... > /data/evals/eval.log 2>&1 &`
- Copy results before tearing down (head boot disk may be `autoDelete: true`)

## 3. Tear Down

```bash
gcloud compute ssh <HEAD_VM> --project=<GCP_PROJECT> --zone=<ZONE> \
  --command="~/venv/bin/ray down ~/opensage/ray/opensage-eval-cluster.yaml -y"
```

## Prewarm Rootfs

Workers must be torn down before RW-mounting the shared disk:

```bash
# Attach, mount, prewarm, detach
gcloud compute instances attach-disk <HEAD_VM> \
  --project=<GCP_PROJECT> --zone=<ZONE> \
  --disk=<ROOTFS_DISK> --device-name=rootfs-shared --mode=rw

gcloud compute ssh <HEAD_VM> --project=<GCP_PROJECT> --zone=<ZONE> --command="
  sudo mkdir -p /mnt/rootfs_shared && \
  sudo mount /dev/disk/by-id/google-rootfs-shared /mnt/rootfs_shared && \
  cd ~/opensage && sudo ~/venv/bin/python ray/prewarm_rootfs.py \
    --workers 4 --backend overlayfs --cache-dir /mnt/rootfs_shared"

gcloud compute ssh <HEAD_VM> --project=<GCP_PROJECT> --zone=<ZONE> \
  --command="sudo umount /mnt/rootfs_shared"
gcloud compute instances detach-disk <HEAD_VM> \
  --project=<GCP_PROJECT> --zone=<ZONE> --disk=<ROOTFS_DISK>
```

## Custom Image

```bash
gcloud compute instances stop <HEAD_VM> --project=<GCP_PROJECT> --zone=<ZONE>
gcloud compute images create opensage-worker-vN \
  --project=<GCP_PROJECT> --source-disk=<HEAD_VM> \
  --source-disk-zone=<ZONE> --family=opensage-worker
gcloud compute instances start <HEAD_VM> --project=<GCP_PROJECT> --zone=<ZONE>
```

`/etc/fstab` must use `nofail` for `/data` — otherwise new VMs without a data disk boot into emergency mode.
