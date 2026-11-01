import argparse
import logging
import os
import subprocess
import sys
import yaml
import json
from jsonschema import validate, ValidationError
import difflib

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class ConfigRollbackValidator:
    """
    Compares a proposed configuration change against a history of past configuration states.
    Identifies potential regressions or unexpected changes introduced by the new configuration.
    """

    def __init__(self, config_history_dir, new_config_path, config_type='yaml'):
        """
        Initializes the ConfigRollbackValidator.

        Args:
            config_history_dir (str): Path to the directory containing historical configuration files.
            new_config_path (str): Path to the new configuration file.
            config_type (str): Type of configuration file (yaml or json). Defaults to 'yaml'.
        """
        self.config_history_dir = config_history_dir
        self.new_config_path = new_config_path
        self.config_type = config_type
        self.logger = logging.getLogger(__name__)

    def _load_config(self, config_path):
        """
        Loads a configuration file based on its type (YAML or JSON).

        Args:
            config_path (str): Path to the configuration file.

        Returns:
            dict: The configuration data as a dictionary.

        Raises:
            ValueError: If the config_type is not supported.
            FileNotFoundError: If the configuration file does not exist.
            YAMLError: If there's an error parsing YAML.
            JSONDecodeError: If there's an error parsing JSON.
        """
        try:
            with open(config_path, 'r') as f:
                if self.config_type.lower() == 'yaml':
                    try:
                        return yaml.safe_load(f)
                    except yaml.YAMLError as e:
                        self.logger.error(f"Error parsing YAML file: {e}")
                        raise
                elif self.config_type.lower() == 'json':
                    try:
                        return json.load(f)
                    except json.JSONDecodeError as e:
                        self.logger.error(f"Error parsing JSON file: {e}")
                        raise
                else:
                    self.logger.error(f"Unsupported configuration type: {self.config_type}")
                    raise ValueError(f"Unsupported configuration type: {self.config_type}")
        except FileNotFoundError:
            self.logger.error(f"Configuration file not found: {config_path}")
            raise

    def _validate_config_syntax(self, config_path):
      """
      Validates the syntax of a YAML or JSON configuration file using yamllint or jsonlint.

      Args:
          config_path (str): The path to the configuration file.

      Returns:
          bool or None: True if the syntax is valid, False if the linter
          reported a syntax failure, and None if the linter binary itself
          is missing, in which case the lint step is skipped and the
          comparison still runs. A missing linter is never reported as a
          syntax failure of the configuration.
      """
      try:
          if self.config_type.lower() == 'yaml':
              linter = 'yamllint'
              result = subprocess.run(['yamllint', config_path], capture_output=True, text=True)
          elif self.config_type.lower() == 'json':
              linter = 'jsonlint'
              result = subprocess.run(['jsonlint', '-q', config_path], capture_output=True, text=True)  # -q for quiet mode
          else:
              self.logger.error(f"Unsupported configuration type: {self.config_type}")
              return False

          if result.returncode == 0:
              self.logger.info(f"Syntax validation successful for {config_path}")
              return True
          else:
              self.logger.error(f"Syntax validation failed for {config_path}:\n{result.stderr}")
              return False

      except FileNotFoundError as e:
          self.logger.warning(f"Missing linter '{linter}': skipping the lint step for {config_path}. The configuration syntax was not checked by {linter}; this is not a syntax failure of the configuration.")
          return None
      except Exception as e:
          self.logger.exception(f"An error occurred during syntax validation: {e}")
          return False

    def compare_with_history(self, sensitivity=0.8):
        """
        Compares the new configuration with historical configurations.

        Args:
            sensitivity (float): A value between 0 and 1 indicating the similarity threshold.
                                 Higher values require a higher degree of similarity.

        Returns:
            list: A list of alerts, each containing a description of the potential regression
                  or unexpected change.  Returns an empty list if no significant deviations
                  are found.
        """
        alerts = []

        # Validate the new config syntax. A missing linter (None) skips
        # the lint step and the comparison still runs; only an actual
        # lint failure (False) aborts the comparison.
        if self._validate_config_syntax(self.new_config_path) is False:
            self.logger.error(f"Syntax validation failed for new configuration: {self.new_config_path}. Aborting comparison.")
            return ["Syntax validation failed for the new configuration. Please correct the syntax errors."]
        try:
            new_config = self._load_config(self.new_config_path)
        except Exception as e:
            self.logger.error(f"Failed to load new configuration: {e}")
            return ["Failed to load new configuration. Please check the configuration file."]

        # Every regular file in the directory is history, whatever its
        # name or suffix, and all of them are parsed as the one config
        # type the operator declared. Sorting is by file name, and
        # nothing treats any entry as more recent than another: the
        # new config is compared against each one in turn.
        history_files = sorted([f for f in os.listdir(self.config_history_dir)
                                if os.path.isfile(os.path.join(self.config_history_dir, f))])

        if not history_files:
            self.logger.warning("No historical configuration files found. Skipping comparison.")
            return ["No historical configurations found.  Unable to perform comparison."]


        for history_file in history_files:
            history_path = os.path.join(self.config_history_dir, history_file)
            # Every history file is linted on every run, one subprocess
            # each, before it is even loaded. In a large history directory
            # that cost is paid in full on every comparison, and a file
            # that fails lint is skipped silently apart from the warning,
            # so it contributes no comparison and no alert.
            if self._validate_config_syntax(history_path) is False:
                self.logger.warning(f"Skipping invalid history file: {history_file}")
                continue

            try:
                historical_config = self._load_config(history_path)
            except Exception as e:
                self.logger.warning(f"Failed to load historical config {history_file}: {e}")
                continue


            # The comparison runs on JSON dumps of the parsed data, in the
            # key order the documents were written in, because json.dumps
            # is not asked to sort. Reordering keys in the new file is
            # therefore a visible change to me even when the settings are
            # identical. A YAML value that JSON cannot represent, a date
            # for instance, raises at this dump, outside the load guard
            # above, and ends the whole comparison in main.
            diff = difflib.ndiff(
                json.dumps(historical_config, indent=2).splitlines(),
                json.dumps(new_config, indent=2).splitlines()
            )
            delta = '\n'.join(diff)

            # Calculate similarity score. Count only diff lines that
            # start with the ndiff plus or minus marker, so plus and
            # hyphen characters inside values are never counted.
            diff_lines = delta.splitlines()
            added_lines = sum(1 for line in diff_lines if line.startswith('+ '))
            removed_lines = sum(1 for line in diff_lines if line.startswith('- '))
            total_lines = len(json.dumps(new_config, indent=2).splitlines()) + len(json.dumps(historical_config, indent=2).splitlines())
            change_ratio = (added_lines + removed_lines) / total_lines if total_lines > 0 else 0

            # Sensitivity enters inverted: at the default 0.8, a change
            # ratio above 0.2 alerts. The ratio itself counts one changed
            # value as two lines, one removed and one added, against the
            # combined length of both documents, so small files cross the
            # line on a single setting change.
            if change_ratio > (1 - sensitivity):  # If the change is significant
                # Each history file gets its own verdict, and the alert carries
                # the full diff text. A new config identical to the oldest
                # snapshot still alerts against the newest one, because I
                # never ask which state a rollback would return to; I only
                # measure distance from every state I was given.
                alerts.append(f"Significant deviation detected compared to {history_file}:\n{delta}")
                self.logger.warning(f"Significant deviation detected compared to {history_file}")
            else:
                self.logger.info(f"Changes compared to {history_file} are within acceptable limits.")
        if alerts:
            return alerts
        else:
            return []


def _sensitivity(value):
    """Argparse type for --sensitivity: a float in the 0 to 1 range."""
    try:
        number = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"sensitivity must be a number between 0 and 1, got {value!r}")
    if not 0.0 <= number <= 1.0:
        raise argparse.ArgumentTypeError(f"sensitivity must be between 0 and 1, got {number}")
    return number


def setup_argparse():
    """Sets up the argument parser."""
    parser = argparse.ArgumentParser(description="Compares a proposed configuration change against a history of past configuration states.")
    parser.add_argument("config_history_dir", help="Path to the directory containing historical configuration files.")
    parser.add_argument("new_config_path", help="Path to the new configuration file.")
    parser.add_argument("--config_type", help="Type of configuration file (yaml or json). Defaults to yaml.", default="yaml")
    parser.add_argument("--sensitivity", type=_sensitivity, default=0.8, help="Similarity threshold (0-1). Higher values require higher similarity. Default: 0.8")
    return parser

def main():
    """Main function to run the configuration rollback validator."""
    parser = setup_argparse()
    args = parser.parse_args()

    # Input validation:  Check if paths exist and are valid.
    if not os.path.isdir(args.config_history_dir):
        logging.error(f"Error: Config history directory '{args.config_history_dir}' does not exist.")
        sys.exit(1)
    if not os.path.isfile(args.new_config_path):
        logging.error(f"Error: New config file '{args.new_config_path}' does not exist.")
        sys.exit(1)

    try:
        validator = ConfigRollbackValidator(args.config_history_dir, args.new_config_path, args.config_type)
        alerts = validator.compare_with_history(args.sensitivity)

        if alerts:
            not_performed = [a for a in alerts if a.startswith("No historical configurations found.")]
            if not_performed and len(not_performed) == len(alerts):
                for alert in alerts:
                    print(f"Comparison not performed: {alert}")
                sys.exit(1)
            print("Potential regressions or unexpected changes detected:")
            for alert in alerts:
                print(alert)
            sys.exit(1)
        else:
            print("No significant deviations detected.")

    except ValueError as e:
        logging.error(f"ValueError: {e}")
        sys.exit(1)
    except Exception as e:
        logging.exception(f"An unexpected error occurred: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()