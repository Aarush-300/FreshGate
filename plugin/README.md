# FreshGate Bifrost plugin

The native plugin build, platform requirements, configuration, and end-to-end test instructions are maintained in the root [README](../README.md#1-bifrost-setup-and-usage).

This Go shared-object plugin runs inside a dynamically linked Bifrost build on Linux or macOS. On Windows, use Ubuntu under WSL2 to build and run Linux Bifrost, or use FreshGate's cross-platform proxy; Windows Bifrost cannot load Go `.so` plugins.
