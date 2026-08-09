# Examples

`extensions.toml` demonstrates the complete manifest model without prescribing
a personal extension inventory. Copy it to
`$XDG_CONFIG_HOME/vscode-exts/extensions.toml`, then adjust bundles and
profiles for the extension hosts you use.

Additional files may live directly under `extensions.d/`. They are fragments,
not independent manifests: bundles append by name, profile `include` lists
append, and each profile's target fields must agree across the aggregate.
