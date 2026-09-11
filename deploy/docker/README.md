# Docker deployment

SimpleOffice4Me can be built and distributed as a Docker image.

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
