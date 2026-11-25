"""Automatic matching QC projections from a single published result."""
from collections import Counter
from functools import cached_property

import numpy as np
from matplotlib.ticker import MaxNLocator

from .automatic_matching import COLORS


PROPORTIONS = "Classification proportions"
VOLUME_HISTOGRAM = "Nuclear volume distributions"
VOLUME_ECDF = "Nuclear volume ECDF"
LINK_COUNTS = "Accepted nuclei per cytoplasmic object"
OVERLAP_HISTOGRAM = "Best and second-best overlap"
DECISION_SPACE = "Classification decision space"
PLOTS = (PROPORTIONS, VOLUME_HISTOGRAM, VOLUME_ECDF, LINK_COUNTS, OVERLAP_HISTOGRAM, DECISION_SPACE)


def draw_volume_histogram(ax, groups, *, bins=30, relative=False, separate=False, colors=None):
    """Render nonempty groups of measured volumes on common bin and axis scales."""
    edges = np.histogram_bin_edges(np.concatenate(list(groups.values())), bins=bins)
    axes = [ax]
    if separate and len(groups) > 1:
        columns = 1 if len(groups) == 2 else 2
        rows = (len(groups) + columns - 1) // columns
        grid = ax.get_subplotspec().subgridspec(rows, columns)
        ax.set_subplotspec(grid[0])
        for index in range(1, len(groups)):
            axes.append(ax.figure.add_subplot(grid[index], sharex=ax, sharey=ax))

    peak = 0
    for index, (name, values) in enumerate(groups.items()):
        panel = axes[index] if separate else ax
        n = len(values)
        heights, _, _ = panel.hist(
            values, bins=edges, weights=np.full(n, 1 / n) if relative else None,
            histtype="step", linewidth=2, label=f"{name} (n={n:,})",
            color=colors[name] if colors else f"C{index}",
        )
        peak = max(peak, heights.max())
    ax.set(xlim=(edges[0], edges[-1]), ylim=(0, peak * 1.05))
    for panel in axes:
        panel.set(xlabel="Nuclear volume (µm³)",
                  ylabel="Within-group fraction per bin" if relative else "Nuclei per bin (count)")
        if not relative:
            panel.yaxis.set_major_locator(MaxNLocator(integer=True))
        panel.legend(fontsize="small")
        panel.grid(alpha=.2)


class MatchingPlotData:
    def __init__(self, result):
        self.result = result

    @cached_property
    def volumes(self):
        groups = {name: [] for name in COLORS}
        for row in self.result.nuclei:
            groups[row.auto_class].append(row.nuclear_volume_um3)
        return {name: np.sort(values) for name, values in groups.items() if values}

    @cached_property
    def links(self):
        return Counter(a.matched_nuclei for a in self.result.associations.values())

    @cached_property
    def fractions(self):
        return np.array([(r.best_nuclear_fraction, r.second_nuclear_fraction)
                         for r in self.result.nuclei]).reshape(-1, 2)

    @cached_property
    def points(self):
        groups = {name: Counter() for name in COLORS}
        for r in self.result.nuclei:
            groups[r.auto_class][r.best_nuclear_fraction, r.second_nuclear_fraction] += 1
        return groups

    def draw(self, ax, kind, **histogram_options):
        result = self.result
        total = len(result.nuclei)
        cyto = len(result.associations)
        ax.set_title(kind)
        if (kind == LINK_COUNTS and not cyto) or (kind != LINK_COUNTS and not total):
            message = "No retained cytoplasmic objects" if kind == LINK_COUNTS else "No retained nuclei"
            ax.text(.5, .5, message, color=ax.xaxis.label.get_color(), ha="center", va="center",
                    transform=ax.transAxes)
            ax.set_xticks([])
            ax.set_yticks([])
            return
        if kind == PROPORTIONS:
            for i, (name, color) in enumerate(COLORS.items()):
                count = result.class_counts[name]
                fraction = count / total
                ax.bar(i, fraction, color=color)
                ax.annotate(f"{count:,} ({fraction:.1%})", (i, fraction), xytext=(0, 4),
                            textcoords="offset points", ha="center", color=ax.xaxis.label.get_color())
            ax.set_xticks(range(len(COLORS)), COLORS)
            ax.set(xlabel="Automatic class", ylabel="Fraction of all retained nuclei", ylim=(0, 1.16))
        elif kind == VOLUME_HISTOGRAM:
            draw_volume_histogram(ax, self.volumes, colors=COLORS, **histogram_options)
            return
        elif kind == VOLUME_ECDF:
            for name, values in self.volumes.items():
                n = len(values)
                unique, counts = np.unique(values, return_counts=True)
                ax.step(np.r_[unique[0], unique], np.r_[0, counts.cumsum() / n],
                        where="post", marker=".", color=COLORS[name], label=f"{name} (n={n:,})")
            ax.set(xlabel="Nuclear volume (µm³)", ylabel="Cumulative fraction within class", ylim=(0, 1.05))
        elif kind == LINK_COUNTS:
            for multiple, label, color in ((False, "Zero or one", "steelblue"), (True, "Multiple (≥2)", "darkorange")):
                xs = sorted(k for k in self.links if (k >= 2) == multiple)
                ax.bar(xs, [self.links[k] for k in xs], color=color,
                       label=f"{label} (n={sum(self.links[k] for k in xs):,})")
            ax.set(xlabel="Accepted nuclei per retained cytoplasmic object", ylabel="Cytoplasmic objects (count)",
                   xlim=(-.6, max(2, max(self.links)) + .6))
            ax.xaxis.set_major_locator(MaxNLocator(integer=True))
            ax.yaxis.set_major_locator(MaxNLocator(integer=True))
        elif kind == OVERLAP_HISTOGRAM:
            for i, label in enumerate(("Best", "Second-best")):
                ax.hist(self.fractions[:, i], bins=np.linspace(0, 1, 26), histtype="step",
                        linewidth=2, label=f"{label} (n={total:,})")
            ax.set(xlabel="Overlap / complete nuclear volume (fraction)", ylabel="Nuclei per bin (count)",
                   xlim=(-.02, 1.02))
            self._thresholds(ax)
        else:
            ax.fill([0, .5, 1], [0, .5, 0], color="slategray", alpha=.25, label="Feasible overlaps")
            for name, counts in self.points.items():
                if counts:
                    x, y = zip(*counts)
                    sizes = 24 + 25 * np.log2(list(counts.values()))
                    ax.scatter(x, y, s=sizes, color=COLORS[name], edgecolors="black", linewidths=.5,
                               alpha=.8, zorder=3, label=f"{name} (n={sum(counts.values()):,})")
            self._thresholds(ax, decision=True)
            ax.set(xlabel="Best overlap / complete nucleus (fraction)",
                   ylabel="Second-best overlap / complete nucleus (fraction)",
                   xlim=(-.035, 1.035), ylim=(-.035, max(.55, result.settings.second_max + .035)))
        if kind != PROPORTIONS:
            ax.legend(fontsize="small")
        ax.grid(alpha=.2)

    def _thresholds(self, ax, *, decision=False):
        settings = self.result.settings
        ax.axvline(settings.match_min, color="deepskyblue", linestyle="--",
                   label=f"Best ≥ {settings.match_min}")
        second_line = ax.axhline if decision else ax.axvline
        second_line(settings.second_max, color="darkorange", linestyle=":",
                    label=f"Second ≤ {settings.second_max}")
