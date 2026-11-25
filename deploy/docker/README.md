# Docker deployment

SimpleOffice4Me can be built and distributed as a Docker image.

The runtime image includes `openssh-client` and `rsync` for outbound SSH-based
source imports. It does not run an SSH server (`sshd`); SSH is used by the
application to connect to registered remote sources.

The image build context excludes local `.env` files and `deploy/docker/secrets/`.
Only the placeholder `deploy/docker/.env.example` is included. Supply real
credentials at runtime through Compose secrets or environment configuration;
never put them in files copied into an image.

## GitHub Actions artifacts

The `Docker build` workflow builds two downloadable image archives on pull requests and manual runs:

- `simpleoffice4me-docker-linux-amd64`
- `simpleoffice4me-docker-linux-arm64`

After downloading and extracting an artifact, load it with:

```sh
docker load < simpleoffice4me-linux-amd64.tar
```

Then run it with persistent data storage:

```sh
docker run --rm -p 8080:8080 \
  -v simpleoffice4me-data:/var/lib/simpleoffice4me \
  simpleoffice4me:pr-<number>-amd64
```

## GitHub Container Registry

Pushes to `main` and version tags publish a multi-architecture image for `linux/amd64` and `linux/arm64` to GitHub Container Registry. Manual workflow runs publish only when started on `main`, the hackathon branch, or a `v*` version tag.

The default branch also receives the `latest` tag. Version tags such as `v1.2.3` retain their tag name. Pushes to `hackathon/contextflow-mvp` publish the separate `hackathon-contextflow-mvp` tag and a commit-specific `sha-*` tag; they do not update `latest` or the stable release-version tag. Pull requests continue to produce downloadable build artifacts without publishing an image.

Pull the current hackathon build with:

```sh
docker pull ghcr.io/jenskapitza/simpleoffice4me:hackathon-contextflow-mvp
```

## Isolated federation test network

`compose.federation-test.yaml` starts two independent SimpleOffice peers and
one dedicated rendezvous relay on an internal Compose network. The three
services have separate named data volumes and identities, no published host
ports, and no route to external networks.
The configured federation tokens are public test-only values; never reuse them
outside this disposable network or put production credentials in this file.

From the repository root, build and start both peers:

```sh
docker compose -f deploy/docker/compose.federation-test.yaml up --build -d
docker compose -f deploy/docker/compose.federation-test.yaml ps
docker compose -f deploy/docker/compose.federation-test.yaml exec -T peer-a \
  python -m tools.federation_testnet_seed
docker compose -f deploy/docker/compose.federation-test.yaml exec -T peer-b \
  python -m tools.federation_testnet_seed
docker compose -f deploy/docker/compose.federation-test.yaml exec -T relay \
  python -m tools.federation_testnet_seed --configure-relay-node
docker compose -f deploy/docker/compose.federation-test.yaml exec -T peer-a \
  python -m tools.federation_testnet_smoke \
  --expect-receive direct_trust_required --relay-action denied
docker compose -f deploy/docker/compose.federation-test.yaml exec -T peer-a \
  python -m tools.federation_testnet_seed \
  --confirm-local-peer --enable-test-relay
docker compose -f deploy/docker/compose.federation-test.yaml exec -T peer-b \
  python -m tools.federation_testnet_seed \
  --confirm-local-peer --enable-test-relay
docker compose -f deploy/docker/compose.federation-test.yaml exec -T peer-a \
  python -m tools.federation_testnet_smoke \
  --expect-receive pending --relay-action send
docker compose -f deploy/docker/compose.federation-test.yaml exec -T peer-b \
  python -m tools.federation_testnet_smoke --expect-receive pending
docker compose -f deploy/docker/compose.federation-test.yaml restart peer-b relay
# Nach "healthy" erneut ausführen:
docker compose -f deploy/docker/compose.federation-test.yaml exec -T peer-a \
  python -m tools.federation_testnet_smoke --expect-receive duplicate
docker compose -f deploy/docker/compose.federation-test.yaml exec -T peer-b \
  python -m tools.federation_testnet_smoke \
  --expect-receive duplicate --relay-action receive
docker compose -f deploy/docker/compose.federation-test.yaml exec -T peer-a \
  python -m tools.federation_testnet_seed --confirm-local-peer
docker compose -f deploy/docker/compose.federation-test.yaml exec -T peer-b \
  python -m tools.federation_testnet_seed --confirm-local-peer
docker compose -f deploy/docker/compose.federation-test.yaml exec -T peer-a \
  python -m tools.federation_testnet_smoke \
  --expect-receive duplicate --relay-action denied
docker compose -f deploy/docker/compose.federation-test.yaml exec -T peer-b \
  python -m tools.federation_testnet_smoke \
  --expect-receive duplicate --relay-action denied
```

Wait until both containers report `healthy` before running the smoke check. It
calls both instances over the internal network and verifies independent
discovery identities, the enabled V3 capability contract, rejection of a
document transfer before trust, and a pending result after an operator
independently confirms the remote peer on each instance. After restarting one
peer, the repeated transfer is recognized as a duplicate from persistent test
data. Peer A also sends a short-lived rendezvous probe through the dedicated
relay to peer B. The relay and peer B restart before peer B receives that probe
exactly once. The sequence first proves that relay traffic is denied without
the separate local opt-in and finally revokes the opt-in on both peers and
proves the denial again. The compatibility bearer cannot authenticate a peer
identity. The seed command writes only to disposable test volumes; its optional
trust flag is a local approval on that instance, not a network pairing
protocol. Each seed run first persists the container's own disposable local
identity (peer id, public URL, shared token from the isolated testnet
environment) into the container-local disk store, because the application
reads no identity environment variables at runtime. Only the three isolated
testnet identities are accepted; anything else fails closed.

The Ubuntu Docker-host CI job runs this same two-container sequence from a
fresh set of volumes. It waits for health before each phase, restarts `peer-b`
and `relay`, verifies that the persisted message is still reported as
`duplicate`, checks relay opt-in, restart persistence, single delivery and
revocation, prints container status and logs on failure, and always removes its
disposable containers, network, and volumes. This is a repeatable process-restart and
persistence check on a hosted Docker Engine; it is not evidence for a production
LAN, external peer, long-running deployment, or hardware acceptance.

For manual discovery diagnosis, the equivalent minimal request is:

```sh
docker compose -f deploy/docker/compose.federation-test.yaml exec -T peer-a \
  python -c "import json,urllib.request; a=json.load(urllib.request.urlopen('http://peer-a:8080/.well-known/simpleoffice-federation')); b=json.load(urllib.request.urlopen('http://peer-b:8080/.well-known/simpleoffice-federation')); assert (a['peer_id'],a['base_url'])==('federation-test-peer-a','http://peer-a:8080'); assert (b['peer_id'],b['base_url'])==('federation-test-peer-b','http://peer-b:8080'); print(a['peer_id'], b['peer_id'])"
```

## Discovery scenarios in the isolated test network

The broadcast LAN scan (`POST /admin/federation/peers/discover/lan`) targets
the host LAN subnets and does not apply inside this internal-only Compose
network. Exercise the same discovery contract with the direct (manual
URL/IP) path instead; LAN, direct, country and email discovery all persist
through the same function, which stores a found peer disabled, without a
token and without data policy, and never retargets an already configured
peer. Trust stays `NONE` until each administrator confirms it locally, and
the V3 capability exchange still requires the per-peer HMAC.

From the peer admin UI (`/admin/federation/peers`), check the other instance
with the **IP / URL** form:

- Enter the peer's private IPv4 literal with port (for example the `peer-b`
  container address with `:8080`). Explicit RFC1918/RFC6598 literals are
  accepted without further opt-in; the connection is pinned to the validated
  address, so DNS rebinding cannot retarget the check.
- To check a DNS name that resolves to a private address (for example
  `http://peer-b:8080`), first enable **Private Ziele erlauben** in the
  network settings on the checking instance. Hostnames stay on the strict
  validation path by design.
- A found peer appears with its compatibility (protocol, confirmed
  capabilities, last check), verification state, trust level and pairing
  status. New peers remain inactive until configured; for a known peer a
  differing discovered address is shown only as a suggestion and must be
  entered manually in the connection editor after review.

The QR payload carries the same identity the well-known profile advertises;
pasting it into the **QR-Payload** form stores only the identity as well.
Pairing codes, where issued, are short-lived, single-use and bound to one
expected peer. Issuing, claiming, checking or revoking a code never enables
a peer, installs a token, grants trust or adds data capabilities. Relay
traffic additionally requires the explicit per-instance relay opt-in proven
by the smoke sequence above.

Stop the peers without removing their test data with:

```sh
docker compose -f deploy/docker/compose.federation-test.yaml down
```

To discard only this Compose project's disposable peer and relay data as well, use
`docker compose -f deploy/docker/compose.federation-test.yaml down --volumes`.
That reset permanently deletes the two test volumes. Do not use it if the data
needs to be retained. This network validates isolated multi-instance startup
and discovery; it does not by itself prove trusted pairing, data exchange, or
LAN broadcast discovery.

### Deployment acceptance: two app containers, real TLS and directional recovery

This is **additional** to the seeded HTTP/relay testnet above. It starts the
regular image and launcher twice on a new internal Linux Docker bridge.
Both peers have their own named app volumes and independent persistent
identity. Both trust the disposable TLS CA for the test, each certificate
has the literal endpoint IP SAN, and the ordinary LAN HTTPS proxy forwards
to each app's loopback-only web service. There are no published ports,
mounted Docker sockets, seeded peer trust or changes to normal federation
policy.

Prerequisites: real Linux Docker Engine, OpenSSL and a supported local Python.
From the repository root:

```sh
scratch="$(mktemp -d)"
python -m pip install --target "$scratch/html" 'beautifulsoup4==4.15.0'
docker build --tag simpleoffice4me:federation-deployment .
python -m tools.smoke_federation_docker \
  --image simpleoffice4me:federation-deployment \
  --html-deps "$scratch/html" \
  --artifacts "$scratch/evidence"
```

The form parser is fixture-only and not installed in the application image.
The runner registers the first admin normally on each host, exchanges and
confirms both QR pairings using ordinary admin forms, sets matching document
grants, then uploads two independent synthetic multi-block files and pulls
one in each direction through the catalog and queue. It verifies the
visible local document link, SHA-256, every byte and the block/download
record. After restarting both containers, identities, job links and bytes
must still match.

For the asymmetric check, both HTTPS endpoints initially work. Both peers
queue fresh downloads, then A's inbound LAN listener alone is stopped with
its ordinary local admin control. A can still pull from B; B's fixed
destination at A is unavailable and its same queued item must report the
visible retry delay. After starting A's listener and waiting until the
actual displayed time, B's one explicit retry must complete. Same-container
HTTP is only used for A's local admin while its public TLS listener is
stopped; cross-peer transfers always use verified TLS. No retry timer is
bypassed. This does not simulate a route firewall, external Wi-Fi/NAT,
arbitrary devices, interrupted partial blocks or production workloads.

The runner removes only the fixture containers, its volumes and internal
network. It retains a timestamped stage timeline, bounded redacted per-peer
event logs, scoped queue/attempt/status counters, and verified result
metadata. Neither raw request logs, authorization, cookies, key files,
peer databases nor document bytes are exported. In CI, the evidence
directory is uploaded even on failure; a missing Docker capability is a
failed acceptance run, not a simulated pass. Read the first failed stage
and the corresponding peer status before deciding whether the issue is
Docker startup, HTTPS, pairing/permissions or queue behavior.
