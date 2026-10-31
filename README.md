![Banner](banner.png)

# misconfig-ConfigRollbackValidator
Compares a proposed configuration change against a history of past configuration states.  Identifies potential regressions or unexpected changes introduced by the new configuration, alerting if the changes deviate significantly from established patterns. - Focused on Check for misconfigurations in configuration files or infrastructure definitions

## Install
`git clone https://github.com/ShadowStrikeHQ/misconfig-configrollbackvalidator`
`pip install -r requirements.txt`

The requirements include the yamllint and jsonlint linters used for the syntax check. If a linter binary is missing at run time, the run names the missing linter, skips that lint step with a warning, and still runs the comparison. A missing linter is never reported as a syntax failure of the configuration.

## Usage
`python3 main.py <config_history_dir> <new_config_path> [--config_type yaml|json] [--sensitivity 0.8]`

The change score counts changed lines: only diff lines that start with the plus or minus marker count, so plus and hyphen characters inside values are never counted.

Exit codes: 0 when the comparison ran and no significant deviation was found, 1 when an alert is returned (a significant deviation, a syntax failure, or a load failure), and 1 with a "Comparison not performed" line when no historical configuration exists to compare against.

## Parameters
- `-h`: Show help message and exit
- `config_history_dir`: Path to the directory containing historical configuration files.
- `new_config_path`: Path to the new configuration file.
- `--config_type`: Type of configuration file (yaml or json). Defaults to yaml.
- `--sensitivity`: Similarity threshold from 0 to 1. Higher values require higher similarity. Values outside 0 to 1 are rejected. Default: 0.8

## License
Copyright (c) ShadowStrikeHQ
