# Remote rendering / 远程渲染

GOO Bridge can send a scene to a machine that has Blender 5.2 and SSH, render it
there, and bring the result back. The remote host does not need GOO Bridge
installed and does not need a Python package beyond Blender's own Python.

## Requirements

- SSH access with a key (password auth is not stored)
- Blender 5.2.x on the remote host
- A writable workspace, by default `~/.goo-bridge`
- `ssh` and `scp` on the client (Windows 10/11 include OpenSSH)
- Enough free space for the scene and its assets

## 1. Configure a key

```bash
ssh-keygen -t ed25519 -C "goo-bridge"
ssh-copy-id render@10.0.0.5
ssh render@10.0.0.5 'blender --version'
```

On Windows without `ssh-copy-id`, append the contents of
`%USERPROFILE%\.ssh\id_ed25519.pub` to `~/.ssh/authorized_keys` on the server.

## 2. Add a profile

```bash
goo-bridge remote add studio \
  --host 10.0.0.5 \
  --user render \
  --port 22 \
  --key ~/.ssh/id_ed25519 \
  --blender blender \
  --workspace ~/.goo-bridge
```

`--blender` is a command on the remote host (`blender` or an absolute path).
The profile is stored in the GOO Bridge config file; the key path is stored,
the key contents are not.

```bash
goo-bridge remote list
goo-bridge remote test --profile studio
```

## 3. Submit a render

```bash
goo-bridge remote render --profile studio scene.blend -o renders/ \
  --camera Camera --samples 32 --resolution 100
```

The client:

1. runs the analyzer and collects external assets;
2. hashes assets with SHA-256 and reuses an existing remote cache;
3. refuses missing assets unless explicitly allowed;
4. uploads the `.blend`, assets, `job.json` and worker;
5. starts Blender headless with the fixed worker script;
6. downloads outputs and verifies their hashes.

Remote layout:

```text
~/.goo-bridge/
  jobs/<job-id>/
    job.json
    scene.blend
    assets/
    worker.py
    output/
```

## 4. Status, fetch and clean

```bash
goo-bridge remote status --profile studio <job-id>
goo-bridge remote fetch --profile studio <job-id> -o renders/
goo-bridge remote clean --profile studio <job-id>
```

The remote worker never runs arbitrary shell commands. It reads `job.json`,
runs Blender with a fixed argument list, and writes a manifest. Paths are
workspace-relative and `..` is rejected.

## Security

- Key-based SSH only; no passwords are written to config or job files
- `StrictHostKeyChecking=accept-new` plus the system `known_hosts`
- Missing assets block a job by default
- Result hashes are verified before the CLI reports success
- Server-side cleanup is limited to the job directory

## Troubleshooting

**`Permission denied (publickey)`** — check `ssh -i <key> user@host` manually;
the profile's `--key` must point to the private key.

**`Blender 5.2 was not found`** — set `--blender /opt/blender-5.2/blender` in
the profile, and make sure it runs non-interactively.

**Windows access violation (0xC0000005)** — a GPU-less Windows server needs
Mesa llvmpipe. See the CI workflow; set `GALLIUM_DRIVER=llvmpipe` and
`LIBGL_ALWAYS_SOFTWARE=1`.

**Uploads are slow** — the first job uploads assets; later jobs reuse the
content cache. Reduce the scene to the needed frame range for previews.

**Missing assets** — run `goo-bridge analyze scene.blend -o analysis.json` and
inspect `missing`; fix the paths rather than forcing a render.
