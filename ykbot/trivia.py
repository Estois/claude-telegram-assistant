"""Animal trivia rotation and its emoji lookup table."""

import json
import random
import re
from pathlib import Path

from ykbot._logging import logger


FALLBACK_TRIVIA = [
    {"animal": "Octopus", "fact": "Octopuses have three hearts and blue blood."},
    {"animal": "Sloth", "fact": "Sloths can take up to a month to digest a single meal."},
    {"animal": "Mantis Shrimp", "fact": "Mantis shrimp can punch with the force of a bullet."},
    {"animal": "Elephant", "fact": "Elephants are one of the few animals that can recognize themselves in a mirror."},
    {"animal": "Wood Frog", "fact": "Wood frogs survive winter by letting up to 65% of their body freeze solid."},
]



class AnimalEmoji:
    """Looks up a representative emoji for an animal name. Checked, in
    order, against exact names, then multi-word phrases, then a dinosaur
    catch-all, then single words (with basic plural stripping) — falling
    back to a generic paw print for anything unmatched. Tuned empirically
    against the ~600 distinct animal names in trivia_cache.json."""

    DEFAULT = "🐾"

    # Portmanteau/proper-noun dog-breed mixes with no matching keyword.
    EXACT = {
        "cockalier": "🐕", "dalmador": "🐕", "dorgi": "🐕", "eskipoo": "🐕",
        "glechon": "🐕", "havashu": "🐕", "labrabull": "🐕", "pomeagle": "🐕",
        "poochon": "🐕", "poogle": "🐕", "pugshire": "🐕", "siberpoo": "🐕",
        "springador": "🐕", "yorkie-poo": "🐕", "jack-chi": "🐕",
        "chorkie": "🐕", "ori-pei": "🐕", "pitsky": "🐕", "siberian": "🐕",
        "french lop": "🐇", "boggle": "🐇", "peruvian inca orchid": "🐕",
        "pocket beagle": "🐕", "chinook": "🐕", "brug": "🐕",
    }

    # Multi-word matches, checked as substrings of the full lowercased name.
    PHRASES = {
        "polar bear": "🐻‍❄️", "black bear": "🐻", "sun bear": "🐻",
        "grizzly bear": "🐻", "asiatic black bear": "🐻", "sea lion": "🦭",
        "sea turtle": "🐢", "sea horse": "🐴", "guide dog": "🦮",
        "killer whale": "🐋", "flying fox": "🦇", "flying squirrel": "🐿️",
        "flying lemur": "🐿️", "hermit crab": "🦀", "king charles spaniel": "🐕",
        "mountain dog": "🐕", "aardwolf": "🐺", "prairie dog": "🐕",
        "mata mata": "🐢", "orang-utan": "🦧", "saint bernard": "🐕",
        "jack russell": "🐕", "tasmanian devil": "🦡", "thorny devil": "🦎",
        "mud dauber": "🐝", "sergeant major": "🐠", "stick insect": "🐛",
        "water bug": "🐛", "bracco italiano": "🐕", "epagneul pont audemer": "🐕",
        "aye-aye": "🐒", "tully monster": "🦕",
    }

    # Single words, checked against each token in the lowercased name.
    WORDS = {
        "dog": "🐕", "puppy": "🐶", "poodle": "🐩", "wolf": "🐺", "fox": "🦊",
        "coyote": "🐺", "dingo": "🐕", "cat": "🐈", "kitten": "🐱", "lion": "🦁",
        "liger": "🦁", "tiger": "🐅", "cheetah": "🐆", "leopard": "🐆",
        "panther": "🐆", "jaguar": "🐆", "jaguarundi": "🐆", "margay": "🐈",
        "lynx": "🐈", "caracal": "🐈", "ocelot": "🐈", "cougar": "🐈",
        "balinese": "🐈", "serval": "🐈", "wildcat": "🐈", "bear": "🐻",
        "panda": "🐼", "koala": "🐨",
        "elephant": "🐘", "rhino": "🦏", "rhinoceros": "🦏", "hippopotamus": "🦛",
        "hippo": "🦛", "giraffe": "🦒", "okapi": "🦒", "zebra": "🦓",
        "zorse": "🦓", "camel": "🐪", "llama": "🦙", "alpaca": "🦙",
        "goat": "🐐", "ibex": "🐐", "markhor": "🐐", "sheep": "🐑",
        "cow": "🐄", "cattle": "🐄", "bull": "🐂", "ox": "🐂", "bison": "🐃",
        "buffalo": "🐃", "zebu": "🐃", "pig": "🐖", "babirusa": "🐗",
        "boar": "🐗", "warthog": "🐗", "horse": "🐴", "mustang": "🐴",
        "donkey": "🫏", "deer": "🦌", "moose": "🫎", "elk": "🦌",
        "reindeer": "🦌", "caribou": "🦌", "gazelle": "🦌", "antelope": "🦌",
        "oryx": "🦌", "kudu": "🦌", "wildebeest": "🐃", "gnu": "🐃",
        "waterbuck": "🦌", "muntjac": "🦌", "takin": "🐐", "gerenuk": "🦌",
        "nyala": "🦌", "saiga": "🦌", "kob": "🦌", "kangaroo": "🦘",
        "wallaby": "🦘", "quokka": "🦘", "raccoon": "🦝", "coati": "🦝",
        "coatimundi": "🦝", "binturong": "🦝", "otter": "🦦", "badger": "🦡",
        "beaver": "🦫", "skunk": "🦨", "polecat": "🦨", "hedgehog": "🦔",
        "tenrec": "🦔", "porcupine": "🦔", "solenodon": "🦔", "squirrel": "🐿️",
        "chipmunk": "🐿️", "marmot": "🐿️", "groundhog": "🐿️", "gopher": "🐿️",
        "mouse": "🐁", "rat": "🐀", "lemming": "🐁", "gerbil": "🐁",
        "hamster": "🐹", "pacarana": "🐹", "mara": "🐹", "rabbit": "🐇",
        "hare": "🐇", "bat": "🦇", "eidolon": "🦇", "monkey": "🐒",
        "baboon": "🐒", "macaque": "🐒", "marmoset": "🐒", "gibbon": "🐒",
        "mandrill": "🐒", "tarsier": "🐒", "lesula": "🐒", "douc": "🐒",
        "gorilla": "🦍", "orangutan": "🦧", "chimp": "🐒", "chimpanzee": "🐒",
        "sloth": "🦥", "megatherium": "🦥", "armadillo": "🦔",
        "anteater": "🦣", "pangolin": "🦔", "hyena": "🐕", "mongoose": "🦫",
        "meerkat": "🦫", "weasel": "🦫", "ferret": "🦦", "ermine": "🦫",
        "wolverine": "🦡", "seal": "🦭", "walrus": "🦭", "dolphin": "🐬",
        "whale": "🐳", "narwhal": "🐳", "manatee": "🦭", "dugong": "🦭",
        "platypus": "🦫", "lemur": "🐒", "loris": "🐒", "civet": "🐈",
        "genet": "🐈", "wombat": "🐨", "opossum": "🐀", "possum": "🐀",
        "numbat": "🐿️", "bilby": "🐇", "echidna": "🦔", "tapir": "🐖",
        "capybara": "🐹", "chinchilla": "🐹", "degu": "🐹", "jerboa": "🐁",
        "pika": "🐇", "dormouse": "🐁", "mole": "🐁", "human": "🧑",
        "fossa": "🐈", "hyrax": "🐹",
        "eagle": "🦅", "hawk": "🦅", "falcon": "🦅", "kite": "🦅", "buzzard": "🦅",
        "owl": "🦉", "vulture": "🦅", "parrot": "🦜", "macaw": "🦜",
        "flamingo": "🦩", "swan": "🦢", "duck": "🦆", "goose": "🦢",
        "wigeon": "🦆", "teal": "🦆", "merganser": "🦆", "chicken": "🐔",
        "hen": "🐔", "rooster": "🐓", "turkey": "🦃", "peacock": "🦚",
        "penguin": "🐧", "waimanu": "🐧", "ostrich": "🦤", "kiwi": "🥝",
        "kakapo": "🦜", "kea": "🦜", "dove": "🕊️", "pigeon": "🕊️",
        "woodpecker": "🐦", "flicker": "🐦", "hummingbird": "🐦",
        "kingfisher": "🐦", "crane": "🐦", "stork": "🐦", "jabiru": "🐦",
        "cardinal": "🐦", "bluebird": "🐦", "robin": "🐦", "jay": "🐦",
        "kingbird": "🐦", "meadowlark": "🐦", "nightjar": "🐦",
        "frigatebird": "🐦", "booby": "🐦", "puffin": "🐧", "cassowary": "🐦",
        "sparrow": "🐦", "finch": "🐦", "warbler": "🐦", "parula": "🐦",
        "waxwing": "🐦", "thrush": "🐦", "chickadee": "🐦", "lyrebird": "🐦",
        "treecreeper": "🐦", "fairywren": "🐦", "wren": "🐦",
        "flycatcher": "🐦", "frogmouth": "🐦", "gull": "🐦", "xeme": "🐦",
        "shoebill": "🦩", "potoo": "🦉", "bird": "🐦", "bustard": "🐦",
        "oxpecker": "🐦", "honeyguide": "🐦", "glider": "🐿️",
        "snake": "🐍", "cobra": "🐍", "viper": "🐍", "python": "🐍",
        "rattlesnake": "🐍", "mamba": "🐍", "adder": "🐍", "anaconda": "🐍",
        "taipan": "🐍", "kingsnake": "🐍", "asp": "🐍", "cascabel": "🐍",
        "keelback": "🐍", "rinkhals": "🐍", "earthsnake": "🐍",
        "lizard": "🦎", "gecko": "🦎", "iguana": "🦎", "skink": "🦎",
        "monitor": "🦎", "chameleon": "🦎", "newt": "🦎", "salamander": "🦎",
        "axolotl": "🦎", "tortoise": "🐢", "turtle": "🐢", "terrapin": "🐢",
        "crocodile": "🐊", "alligator": "🐊", "caiman": "🐊", "gharial": "🐊",
        "sarcosuchus": "🐊", "dragon": "🐉", "meganeura": "🐉",
        "dinosaur": "🦖", "raptor": "🦖", "thalassomedon": "🦕",
        "thanatosdrakon": "🦕", "frog": "🐸", "toad": "🐸", "bullfrog": "🐸",
        "caecilian": "🐸",
        "shark": "🦈", "fish": "🐟", "pufferfish": "🐡", "blowfish": "🐡",
        "angelfish": "🐠", "catfish": "🐟", "goldfish": "🐠", "sunfish": "🐟",
        "swordfish": "🐟", "sailfish": "🐟", "triggerfish": "🐠",
        "filefish": "🐠", "blobfish": "🐟", "lungfish": "🐟", "stonefish": "🐟",
        "fangtooth": "🐟", "fringehead": "🐟", "gar": "🐟", "mojarra": "🐟",
        "cichlid": "🐠", "betta": "🐠", "tang": "🐠", "grouper": "🐟",
        "arowana": "🐟", "pollock": "🐟", "tuna": "🐟", "archerfish": "🐟",
        "coelacanth": "🐟", "hagfish": "🐟", "lamprey": "🐟", "eel": "🐟",
        "moray": "🐟", "ray": "🐟", "rays": "🐟", "sawfish": "🦈",
        "wolffish": "🐟", "cod": "🐟", "pacu": "🐟", "piranha": "🐟",
        "tigerfish": "🐟", "perch": "🐟", "sucker": "🐟",
        "squid": "🦑", "octopus": "🐙", "jellyfish": "🪼", "crab": "🦀",
        "lobster": "🦞", "shrimp": "🦐", "seahorse": "🐴", "anemone": "🌊",
        "starfish": "⭐", "snail": "🐌", "slug": "🐌", "cone": "🐚",
        "bee": "🐝", "wasp": "🐝", "hornet": "🐝", "ant": "🐜", "beetle": "🪲",
        "weevil": "🪲", "butterfly": "🦋", "moth": "🦋", "dragonfly": "🐉",
        "mosquito": "🦟", "cricket": "🦗", "grasshopper": "🦗", "locust": "🦗",
        "mantis": "🦗", "cicada": "🦗", "cockroach": "🪳", "earwig": "🪲",
        "millipede": "🐛", "centipede": "🐛", "spider": "🕷️", "tarantula": "🕷️",
        "scorpion": "🦂", "worm": "🪱", "earthworm": "🪱", "superworm": "🪱",
        "caterpillar": "🐛", "fly": "🪰", "flies": "🪰", "aphid": "🪲",
        "aphids": "🪲", "woodlouse": "🐛", "insect": "🐛", "birdeater": "🕷️",
        "armyworm": "🐛",
        # Dog breed / kennel-club keywords.
        "terrier": "🐕", "hound": "🐕", "coonhound": "🐕", "spaniel": "🐕",
        "retriever": "🐕", "shepherd": "🐕", "corgi": "🐕", "husky": "🐕",
        "chow": "🐕", "heeler": "🐕", "elkhound": "🐕", "newfoundland": "🐕",
        "newfie": "🐕", "laika": "🐕", "schnauzer": "🐕", "malamute": "🐕",
        "mastiff": "🐕", "chihuahua": "🐕", "beagle": "🐕", "dachshund": "🐕",
        "whippet": "🐕", "greyhound": "🐕", "wolfhound": "🐕", "bulldog": "🐕",
        "rottweiler": "🐕", "doberman": "🐕", "boxer": "🐕", "papillon": "🐕",
        "vizsla": "🐕", "weimaraner": "🐕", "akita": "🐕", "shiba": "🐕",
        "sheepdog": "🐕", "setter": "🐕", "pointer": "🐕", "collie": "🐕",
        "basenji": "🐕", "schipperke": "🐕", "schapendoes": "🐕",
        "pekingese": "🐕", "havanese": "🐕", "otterhound": "🐕",
        "akbash": "🐕", "gampr": "🐕", "zuchon": "🐕",
    }

    # Dog-breed keywords take priority over incidental descriptive words
    # elsewhere in the name — "American Leopard Hound" and "Bull Terrier"
    # are dog breeds, not a leopard or a bull, but a first-match-wins scan
    # of the full WORDS table would hit "leopard"/"bull" first.
    BREED_WORDS = frozenset(
        {
            "terrier", "hound", "coonhound", "spaniel", "retriever", "shepherd",
            "corgi", "husky", "chow", "heeler", "elkhound", "newfoundland",
            "newfie", "laika", "schnauzer", "malamute", "mastiff", "chihuahua",
            "beagle", "dachshund", "whippet", "greyhound", "wolfhound", "bulldog",
            "rottweiler", "doberman", "boxer", "papillon", "vizsla", "weimaraner",
            "akita", "shiba", "sheepdog", "setter", "pointer", "collie", "basenji",
            "schipperke", "schapendoes", "pekingese", "havanese", "otterhound",
            "akbash", "gampr", "zuchon",
        }
    )

    @classmethod
    def for_name(cls, name: str) -> str:
        lowered = name.lower()
        if lowered in cls.EXACT:
            return cls.EXACT[lowered]
        for phrase, emoji in cls.PHRASES.items():
            if phrase in lowered:
                return emoji
        if "saur" in lowered:
            return "🦕"

        words = re.findall(r"[a-z']+", lowered)
        if any(word in cls.BREED_WORDS for word in words):
            return "🐕"
        for word in words:
            if word in cls.WORDS:
                return cls.WORDS[word]
            if word.endswith("s") and word[:-1] in cls.WORDS:
                return cls.WORDS[word[:-1]]
        return cls.DEFAULT



class TriviaBook:
    """Serves animal trivia facts in a shuffled, non-repeating rotation.

    Walks a shuffled permutation of the pool so nothing repeats until every
    fact has been used once, then reshuffles for the next cycle.
    """

    def __init__(self, cache_file: Path, state_file: Path, fallback: list):
        self._cache_file = cache_file
        self._state_file = state_file
        self._fallback = fallback

    def next_fact(self) -> str:
        pool = self._load_cache() or self._fallback
        item = pool[self._next_index(len(pool))]
        emoji = AnimalEmoji.for_name(item["animal"])
        return f"{emoji} {item['animal']} fact: {item['fact']}"

    def _load_cache(self) -> list:
        if not self._cache_file.exists():
            return []
        try:
            return json.loads(self._cache_file.read_text())
        except json.JSONDecodeError:
            logger.exception("%s is corrupt", self._cache_file)
            return []

    def _next_index(self, pool_size: int) -> int:
        state = {}
        if self._state_file.exists():
            try:
                state = json.loads(self._state_file.read_text())
            except json.JSONDecodeError:
                state = {}

        order = state.get("order") or []
        position = state.get("position", 0)

        if len(order) != pool_size or position >= len(order):
            order = list(range(pool_size))
            random.shuffle(order)
            position = 0

        index = order[position]
        self._state_file.write_text(json.dumps({"order": order, "position": position + 1}))
        return index

