# Docker deployment

SimpleOffice4Me can be built and distributed as a Docker image.

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

Pushes to `main` and version tags publish a multi-architecture image for `linux/amd64` and `linux/arm64` to GitHub Container Registry.

The default branch also receives the `latest` tag. Version tags such as `v1.2.3` retain their tag name.

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
protocol.

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
