# PatchAgent baseline experiments

| Script | Description | loc | knowledge | test feedback |
| --- | --- | --- | --- | --- |
| `run_exp1.sh` | Default | w. | w. | w.o. |
| `run_exp2.sh` | Reserved exp2 entry (same dataset as exp1) | w. | w. | w.o. |
| `run_exp3.sh` | No location | w.o. | w. | w.o. |
| `run_exp4.sh` | No knowledge | w. | w.o. | w.o. |
| `run_exp5.sh` | Blackbox (no location) | w.o. | w. | w.o. |

After generation, run:

```bash
bash shells/run_eval.sh <prefix> 
```

one-time repo preparation:

```bash
bash shells/prepare_repos.sh
```
