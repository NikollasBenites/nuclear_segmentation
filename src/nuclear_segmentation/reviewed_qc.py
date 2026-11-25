"""Comparisons over all retained nuclei in the selected matching pair."""
from collections import Counter, defaultdict
import numpy as np

from .association_review import reviewed_outcomes

REVIEW_PLOTS = ("Automatic and reviewed outcomes", "Review completion and changes",
                "Multiple associations before and after review", "Reviewed nuclear volumes", "Reviewed nuclear ECDF")


def draw_review(ax, kind, result, review):
    outcomes = list(reviewed_outcomes(result, review))
    n = len(outcomes)
    ax.set_title(f"{kind} (n={n} nuclei)")
    if not n and kind != REVIEW_PLOTS[2]:
        ax.text(.5,.5,"No retained nuclei",ha="center",transform=ax.transAxes)
        return
    counts = Counter(status for _,status,_ in outcomes)
    if kind == REVIEW_PLOTS[0]:
        values = {**{f"Auto {k}":v for k,v in result.class_counts.items()}, **dict(counts)}
    elif kind == REVIEW_PLOTS[1]:
        complete = n - counts["Not Reviewed"] - counts["Requires Review"]
        changed = sum(s in ("Matched","Unmatched","Uncertain") and
                      (s != {"MATCH":"Matched","NO_MATCH":"Unmatched","AMBIGUOUS":"Uncertain"}[r.auto_class]
                       or t != r.accepted_cytoplasmic_id) for r,s,t in outcomes)
        values = {"Reviewed":complete,"Unreviewed":counts["Not Reviewed"],"Stale":counts["Requires Review"],
                  "Uncertain":counts["Uncertain"],"Changed":changed}
    elif kind == REVIEW_PLOTS[2]:
        links = Counter(t for _,s,t in outcomes if s == "Matched")
        values = {"Automatic":result.multiple_objects, "Reviewed":sum(v>1 for v in links.values())}
        ax.set_title(f"Multiple links among {len(result.associations)} retained cytoplasmic objects")
    else:
        groups = defaultdict(list)
        for row,status,_ in outcomes:
            groups[status].append(row.nuclear_volume_um3)
        bins = np.histogram_bin_edges([r.nuclear_volume_um3 for r,_,_ in outcomes], bins=30)
        for status, volumes in groups.items():
            label = f"{status} (n={len(volumes)})"
            if kind == REVIEW_PLOTS[-1]:
                ax.step(np.sort(volumes),np.arange(1,len(volumes)+1)/len(volumes),where="post",label=label)
            else:
                ax.hist(volumes,bins=bins,histtype="step",label=label,weights=np.full(len(volumes),1/len(volumes)))
        ax.set_xlabel("Nuclear volume (µm³)")
        ax.set_ylabel("Cumulative fraction" if kind == REVIEW_PLOTS[-1] else "Within-group fraction")
        ax.legend()
        return
    ax.bar(list(values), list(values.values()))
    ax.tick_params(axis="x", labelrotation=35)
    ax.set_ylabel("Cytoplasmic objects" if kind == REVIEW_PLOTS[2] else "Nuclei")
