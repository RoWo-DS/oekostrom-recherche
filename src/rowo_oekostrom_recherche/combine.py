from typing import NewType, cast
from rowo_oekostrom_recherche.scraper import (
    base,
    okpower,
    oekotest,
    rowo_2019,
    stromauskunft,
    verivox,
)
from thefuzz import process
from pydantic import Field
from typing_extensions import TypedDict, Literal

from rowo_oekostrom_recherche.scraper.base import NameNormal

Source = NewType("source", str)

SELECTION_FILE = base.DATA_DIR / "combine_selections.csv"
TARGET = Source("rowo2019")


class SourceData(TypedDict, total=False):
    oekotest: oekotest.Oekotest
    okpower: okpower.OkPower
    stromauskunft: stromauskunft.Stromauskunft
    verivox: verivox.VerivoxBase


class Combined(rowo_2019.RoWo):
    rowo2019: bool = True
    sources: SourceData = Field(default_factory=SourceData)


class LoadedSourceData(SourceData):
    rowo_2019: Combined


SOURCE_TYPES: dict[Source, type[base.AnbieterBase]] = {
    Source(k): cast(type[base.AnbieterBase], v)
    for k, v in LoadedSourceData.__annotations__.items()
}


def to_keydict(
    scrape_results: base.ScrapeResults,
) -> dict[NameNormal, base.AnbieterBase]:
    results: dict[NameNormal, base.AnbieterBase] = {}
    duplicates: dict[NameNormal, list[base.AnbieterBase]] = {}
    duplicate_keys: set[NameNormal] = set()
    for r in scrape_results.results:
        name = r.name_normalized
        duplicates.setdefault(name, []).append(r)
        if name in results:
            duplicate_keys.add(name)
        results[name] = r
    if scrape_results.source == "oekotest":
        duplicate_keys.remove(NameNormal("westfalenwind"))
    if duplicate_keys:
        for key in sorted(duplicate_keys):
            print(f" -> {key} ({scrape_results.source})")
            for obj in duplicates[key]:
                print(f"      -> {obj}")
        raise ValueError("Duplicate normalized names")
    return results


def load_data() -> dict[Source, dict[NameNormal, base.AnbieterBase]]:
    loaded_data: dict[Source, dict[NameNormal, base.AnbieterBase]] = {}
    for source_file in base.DATA_DIR.glob("*.json"):
        source = Source(source_file.name.removesuffix(".json"))
        target_type: type[base.AnbieterBase]
        if source == TARGET:
            target_type = Combined
        else:
            target_type = SOURCE_TYPES[source]
        scrape_results = base.ScrapeResults[target_type].model_validate_json(
            source_file.read_text()
        )
        loaded_data[source] = to_keydict(scrape_results)

    return loaded_data


def load_selections() -> dict[tuple[Source, str], str | None]:
    """
    Load selections that have been already done
    """
    selections: dict[tuple[Source, str], str | None] = {}
    if SELECTION_FILE.exists():
        for line in SELECTION_FILE.read_text().splitlines(keepends=False):
            choice: str | None
            source, anbieter, choice = line.split(";")
            if choice == "":
                choice = None
            selections[(source, anbieter)] = choice
    return selections


def input_selection(choices: list[NameNormal]) -> NameNormal | None | Literal[-1]:
    while True:
        try:
            result = input("> ").lower()
            if result == "" and len(choices) == 1:
                return choices[0]
            if result == "x":
                return None
            if result == "s":
                return -1
            if result == "q":
                print("Selected to exit")
                raise KeyboardInterrupt()
            return choices[int(result)-1]
        except (ValueError, IndexError):
            print(
                "Invalid input. Try again. Input must be number between 1 and 4 or x or q."
            )


def extract_combination(
    source: Source,
    data_source: base.AnbieterBase,
    check_for: NameNormal,
    check_against: dict[NameNormal, Combined],
    full_names_to_val: dict[str, Combined],
    taken_choices: set[NameNormal],
) -> Combined | None | Literal[-1]:
    selections = load_selections()
    if (source, data_source.name) in selections:
        pre_result = selections[(source, data_source.name)]
        if pre_result == "-1":
            return -1
        if pre_result is None:
            return None
        return full_names_to_val[pre_result]
    candidates = process.extractBests(
        check_for, set(check_against.keys()), limit=20, score_cutoff=75
    )
    if (
        candidates[0][1] > 95
        and (len(candidates) == 1 or candidates[1][1] <= 90)
        and candidates[0][0] not in taken_choices
    ):
        print(f" -> Selected  {check_against[candidates[0][0]]}")
        print(f"    ↪    for  {data_source}\n")
        return check_against[candidates[0][0]]
    print(f"Looking for match: {data_source}")
    for i, candidate in enumerate(candidates, start=1):
        dup = "!taken already!" if candidate[0] in taken_choices else ""
        indent = " " * 5
        print(
            f" ({i:>2}) [{candidate[1]:>3} %] {dup}{indent}{check_against[candidate[0]]}"
        )
    print(" (x) Add as new entry (q to quit, s to skip)")
    selection = input_selection([candidate[0] for candidate in candidates])
    if selection == -1 or selection is None:
        with SELECTION_FILE.open("a") as f:
            f.write(f"{source};{data_source.name};{selection or ''}\n")
        return selection
    result = check_against[selection]
    with SELECTION_FILE.open("a") as f:
        f.write(f"{source};{data_source.name};{result.name}\n")
    return


def combine() -> None:
    sources_data = load_data()
    target_data = cast(dict[NameNormal, Combined], sources_data[TARGET])
    target_data_plz: dict[NameNormal, Combined] = {
        v.name_normalized_plz: v for v in target_data.values()
    }
    full_names_to_val: dict[str, Combined] = {
        v.name: v for v in target_data_plz.values()
    }
    found: int = 0
    skipped: int = 0
    added: int = 0
    try:
        for source, anbieter_dict in sources_data.items():
            if source == TARGET:
                continue
            print("#" * 120)
            print(f"# Finding connection for {source}")
            print("#" * 120)
            taken_choices: set[NameNormal] = set()
            for anbieter_name, source_data in anbieter_dict.items():
                check_for = anbieter_name
                check_against = target_data
                if source_data.plz:
                    check_for = NameNormal(f"{source_data.plz} {anbieter_name}")
                    check_against = target_data_plz
                selection = extract_combination(
                    source=source,
                    data_source=source_data,
                    check_for=check_for,
                    check_against=check_against,
                    full_names_to_val=full_names_to_val,
                    taken_choices=taken_choices,
                )
                if selection == -1:
                    skipped += 1
                    continue
                elif selection:
                    found += 1
                    taken_choices.add(selection.name_normalized)
                    selection.sources[source] = sources_data
                else:
                    # add new entry as it was missing in original data
                    added += 1
                    new_obj = Combined.model_validate(
                        {
                            **source_data.model_dump(),
                            "rowo2019": False,
                            "sources": {source: source_data},
                        },
                        strict=False,
                    )
                    target_data[anbieter_name] = new_obj
                    target_data_plz[new_obj.name_normalized_plz] = new_obj
    except KeyboardInterrupt:
        print(f"{found=}, {skipped=}, {added=}, exiting")


if __name__ == "__main__":
    combine()
