# Here is the source code of all agents

It includes **SWEagent, OpenHands, ClaudeCode, and PatchAgent baseline**. You can navigate into the corresponding folder and run experiments according to each `README.md`.

# The experimental setup is as follows


| Experiment  | Description                                                 | loc | knowledge | Test feedback |
| ----------- | ----------------------------------------------------------- | --- | --------- | ------------- |
| 1 (default) | Only location information is provided                       | w.  | w.        | wo.           |
| 2           | Adds feedback for testing                                   | w.  | w.        | w.            |
| 3           | Adds feedback for testing, but without location information | wo. | w.        | w.            |
| 4           | No knowledge information provided                           | w.  | wo.       | wo.           |
| 5           | Blackbox                                                    | wo. | w.        | wo            |


