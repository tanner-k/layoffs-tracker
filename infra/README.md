# Running the tracker on storageguy

Single-node k3s, Airflow from the official Helm chart, one monthly DAG. Commands run on
`storageguy` unless marked **Mac**. Steps marked **Tanner** need sudo or a secret; nobody
else runs them.

## 0. Before go-live: the SEC port check (Mac)

From the repo root, with `SEC_USER_AGENT` exported from your own `.env`:

```bash
set -a; source path/to/.env; set +a
uv run python scripts/port_check.py
```

Expected: `only live: []` and `only paper: []`. A difference is explained or fixed first.

Then compare the tracker's Jev labeler with the paper's Jev run on the same filings (a few Jev
calls):

```bash
uv run python scripts/jev_check.py
```

If the tracker's counts differ much from the paper's Jev run (17 AI-cited, 14 unresolved), say so
on the page before go-live.

## 1. k3s and Helm (Tanner, sudo)

```bash
curl -sfL https://get.k3s.io | sh -
mkdir -p ~/.kube && sudo cp /etc/rancher/k3s/k3s.yaml ~/.kube/config && sudo chown "$USER": ~/.kube/config
sudo snap install helm --classic
kubectl get nodes   # one node, Ready
uname -m            # x86_64: the image is built for linux/amd64
```

k3s brings its own containerd, local-path storage and metrics-server; the existing Docker is
untouched.

## 2. Namespace, work volume, repo checkout

```bash
git clone https://github.com/tanner-k/layoffs-tracker.git ~/layoffs-tracker && cd ~/layoffs-tracker
kubectl apply -f infra/namespace.yaml -f infra/pvc.yaml
```

## 3. Secrets (Tanner)

Deploy key (write access to this repo only), made on the server:

```bash
ssh-keygen -t ed25519 -N "" -C "layoffs-tracker@storageguy" -f ~/layoffs-deploy-key
kubectl create secret generic layoffs-deploy-key -n airflow \
  --from-file=id_ed25519=$HOME/layoffs-deploy-key \
  --from-file=known_hosts=<(ssh-keyscan github.com 2>/dev/null)
cat ~/layoffs-deploy-key.pub
```

Add the printed public key at github.com/tanner-k/layoffs-tracker → Settings → Deploy keys,
with **Allow write access**. Then delete the private key file: `rm ~/layoffs-deploy-key`.

API secrets, from your Mac's `.env` (values unquoted; nothing is echoed) — **Mac**:

```bash
grep -E '^(TYPESAFE_API_KEY|SEC_USER_AGENT)=' path/to/.env \
  | ssh storageguy 'kubectl create secret generic layoffs-secrets -n airflow --from-env-file=/dev/stdin'
```

Phone alerts. Install the **ntfy** app on your phone, then make an unguessable topic. The topic
name is the only thing protecting it, so it lives in a Secret and nowhere else:

```bash
T="layoffs-$(openssl rand -hex 12)"
kubectl create secret generic layoffs-alerts -n airflow --from-literal=url="https://ntfy.sh/$T"
echo "In the ntfy app, subscribe to: $T"
curl -s -d "Test from storageguy" -H "Title: Layoffs tracker: test" "https://ntfy.sh/$T" >/dev/null
unset T
```

Your phone should buzz with "Layoffs tracker: test". Create this Secret before step 4: every
Airflow pod reads it and will not start without it.

## 4. Airflow

```bash
helm repo add apache-airflow https://airflow.apache.org && helm repo update
read -rsp "Airflow admin password: " AIRFLOW_ADMIN_PW; echo
read -rsp "Postgres password (keep it: every upgrade needs the same one): " PG_PW; echo
helm upgrade --install airflow apache-airflow/airflow --version 1.22.0 -n airflow \
  -f infra/airflow-values.yaml \
  --set-string createUserJob.defaultUser.password="$AIRFLOW_ADMIN_PW" \
  --set-string postgresql.auth.postgresPassword="$PG_PW" \
  --set-string data.metadataConnection.pass="$PG_PW" \
  --set-string config.api.base_url="https://$(tailscale status --json | jq -r .Self.DNSName | sed 's/\.$//'):8443" \
  --timeout 15m
unset AIRFLOW_ADMIN_PW PG_PW
kubectl get pods -n airflow   # all Running (jobs Completed)
kubectl get pvc -n airflow    # layoffs-work and the chart's volumes all Bound
kubectl auth can-i create pods -n airflow \
  --as=system:serviceaccount:airflow:airflow-scheduler   # yes: tasks launch pods
```

The two `--set-string` lines for Postgres must match: the chart's database takes the first,
Airflow connects with the second. `base_url` makes the log links in failure alerts open the
dashboard from step 6.

Check the DAG loaded:

```bash
S=$(kubectl get pod -n airflow -l component=scheduler -o name)
kubectl exec -n airflow "$S" -c scheduler -- airflow dags list-import-errors
kubectl exec -n airflow "$S" -c scheduler -- airflow dags list | grep layoffs_monthly
```

## 5. The image (Tanner, one click)

After the first merge to `main`, the Image workflow pushes `ghcr.io/tanner-k/layoffs-tracker`.
GitHub makes new packages private: open the package → Package settings → Change visibility →
Public, so k3s pulls it without credentials. Check from storageguy:

```bash
sudo k3s crictl pull ghcr.io/tanner-k/layoffs-tracker:main
```

## 6. Dashboard over Tailscale (never public)

The API server stays `ClusterIP`. Tailscale serves it on port 8443 to your tailnet only, next to
the app you already serve at `/`:

```bash
IP=$(kubectl get svc -n airflow airflow-api-server -o jsonpath='{.spec.clusterIP}')
sudo tailscale serve --bg --https=8443 "http://$IP:8080"
tailscale serve status   # :8443 (tailnet only) and your existing / route
```

Open `https://storageguy.<your-tailnet>.ts.net:8443` from the Mac or the phone, with Tailscale
on, and sign in as `admin`. Never use `tailscale funnel` for it; that would make it public.
`kubectl get svc -n airflow` must show no `NodePort` or `LoadBalancer`. If the Helm release is
ever reinstalled, the service gets a new IP: run the two lines again.

Fallback without Tailscale serve, from the **Mac**:

```bash
ssh -L 8080:localhost:8080 storageguy 'kubectl port-forward -n airflow svc/airflow-api-server 8080:8080'
```

## 7. Measure memory

```bash
free -h
kubectl top pods -n airflow
```

Target: under 3 GB in use for k3s plus Airflow. Over it, lower the limits in
`infra/airflow-values.yaml`, `helm upgrade` again (same command), and re-measure. Record the
numbers in the top-level README.

## 8. Go live

Unpause `layoffs_monthly` in the dashboard. Catch-up runs the 2026-11-03 slot (data month
2026-10). The `publish` task pushes `data: refresh through 2026-10` to `main`.

What the phone tells you each month:

| Alert | Meaning |
|---|---|
| "Layoffs tracker: 2026-10 done" (low priority) | All six steps succeeded |
| "Layoffs tracker: label failed" (high priority) | That step failed after its 2 retries; nothing was published. The alert links to the log |
| No alert by the 4th | The run did not happen: open the dashboard |

Retries are silent; only the last failure alerts. During the first
run, watch for pods killed for memory:

```bash
kubectl get events -n airflow --watch | grep -i -E "oom|evict"
```

## Notes

- **Branch protection:** `publish` pushes to `main` with the deploy key. If you protect `main`,
  let the deploy key bypass the rule, or the push fails (loudly; nothing half-published).
- **Postgres password:** set once in step 4 and needed again on every upgrade. An upgrade
  without it puts Airflow's side back to the chart default, and Airflow can no longer connect.
- **Upgrades:** repeat the step 4 command with the same passwords. Helm regenerates the chart's own random keys; with
  no stored Airflow connections that only signs you out.
