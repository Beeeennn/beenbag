import discord
MOBS = {"Zombie":{"rarity":1,"hostile":True},
        "Enderman":{"rarity":3,"hostile":True},
        "Cow":{"rarity":1,"hostile":False},
        "Chicken":{"rarity":1,"hostile":False},
        "Armadillo":{"rarity":2,"hostile":False},
        "Cod":{"rarity":1,"hostile":True},
        "Axolotl":{"rarity":2,"hostile":False},
        "Dolphin":{"rarity":2,"hostile":False},
        "Camel":{"rarity":2,"hostile":False},
        "Donkey":{"rarity":1,"hostile":False},
        "Frog":{"rarity":3,"hostile":False},
        "Fox":{"rarity":2,"hostile":False},
        "Snow Fox":{"rarity":4,"hostile":False},
        "Glow Squid":{"rarity":2,"hostile":True},
        "Goat":{"rarity":3,"hostile":False},
        "Hoglin":{"rarity":4,"hostile":True},
        "Horse":{"rarity":1,"hostile":False},
        "Llama":{"rarity":2,"hostile":False},
        "Mooshroom":{"rarity":4,"hostile":False},
        "Ocelot":{"rarity":3,"hostile":False},
        "Panda":{"rarity":3,"hostile":False},
        "Brown Panda":{"rarity":4,"hostile":False},
        "Parrot":{"rarity":2,"hostile":False},
        "Pig":{"rarity":1,"hostile":False},
        "Sheep":{"rarity":1,"hostile":False},
        "Polar Bear":{"rarity":1,"hostile":True},
        "Pufferfish":{"rarity":2,"hostile":True},
        "Salmon":{"rarity":1,"hostile":True},
        "Squid":{"rarity":1,"hostile":True},
        "Strider":{"rarity":2,"hostile":False},
        "Tropical Fish":{"rarity":3,"hostile":True},
        "Turtle":{"rarity":1,"hostile":False},
        "Wolf":{"rarity":1,"hostile":False},
        "Cat":{"rarity":1,"hostile":False},
        "Allay":{"rarity":3,"hostile":False},
        "Bat":{"rarity":2,"hostile":True},
        "Mule":{"rarity":1,"hostile":False},
        "Skeleton Horse":{"rarity":4,"hostile":True},
        "Sniffer":{"rarity":5,"hostile":False},
        "Snow Golem":{"rarity":4,"hostile":True},
        "Tadpole":{"rarity":1,"hostile":False},
        "Bee":{"rarity":1,"hostile":False},
        "Cave Spider":{"rarity":1,"hostile":True},
        "Drowned":{"rarity":1,"hostile":True},
        "Iron Golem":{"rarity":3,"hostile":True},
        "Piglin":{"rarity":2,"hostile":True},
        "Spider":{"rarity":1,"hostile":True},
        "Zombie Pigman":{"rarity":1,"hostile":True},
        "Sea Pickle":{"rarity":5,"hostile":False},
        "Blaze":{"rarity":2,"hostile":True},
        "Bogged":{"rarity":1,"hostile":True},
        "Breeze":{"rarity":3,"hostile":True},
        "Creaking":{"rarity":4,"hostile":True},
        "Creeper":{"rarity":1,"hostile":True},
        "Elder Guardian":{"rarity":5,"hostile":True},
        "Ender Dragon":{"rarity":5,"hostile":True},
        "Evoker":{"rarity":3,"hostile":True},
        "Ghast":{"rarity":1,"hostile":True},
        "Guardian":{"rarity":2,"hostile":True},
        "Husk":{"rarity":1,"hostile":True},
        "Magma Cube":{"rarity":1,"hostile":True},
        "Phantom":{"rarity":1,"hostile":True},
        "Pillager":{"rarity":1,"hostile":True},
        "Ravager":{"rarity":2,"hostile":True},
        "Shulker":{"rarity":2,"hostile":True},
        "Silverfish":{"rarity":2,"hostile":True},
        "Skeleton":{"rarity":1,"hostile":True},
        "Slime":{"rarity":1,"hostile":True},
        "Stray":{"rarity":2,"hostile":True},
        "Vex":{"rarity":3,"hostile":True},
        "Warden":{"rarity":5,"hostile":True},
        "Witch":{"rarity":1,"hostile":True},
        "Wither":{"rarity":5,"hostile":True},
        "Wither Skeleton":{"rarity":2,"hostile":True},
        "Zoglin":{"rarity":3,"hostile":True},
        "Zombie Villager":{"rarity":1,"hostile":True},
        "Copper Golem":{"rarity":3,"hostile":True},
        "Happy Ghast":{"rarity":3,"hostile":True}
        }
# IDs of channels where !yt is permitted
LINK_CHANNELS = [1395577501916336128, 1396194783713824800]
RARITIES ={
    1:{"colour":"white","name":"common","wheat":10,"emeralds":1,"stay":180},
    2:{"colour":"green","name":"uncommon","wheat":20,"emeralds":2,"stay":160},
    3:{"colour":"blue","name":"rare","wheat":30,"emeralds":3,"stay":120},
    4:{"colour":"purple","name":"epic","wheat":50,"emeralds":5,"stay":90},
    5:{"colour":"red","name":"legendary","wheat":80,"emeralds":10,"stay":60}
}
WHEAT_DROP ={None: 2,
            "wood":   3,
            "stone":   4,
            "iron":    5,
            "gold":    6,
            "diamond": 7
}
ANNOUNCE_CHANNEL_ID = 1396194783713824800
# Define weighted drop tables per pickaxe tier
DROP_TABLES = {
    "wood":    {"cobblestone": 80, "iron": 15, "gold": 4,  "diamond": 1},
    "stone":   {"cobblestone": 70, "iron": 20, "gold": 8, "diamond": 2},
    "iron":    {"cobblestone": 50, "iron": 30, "gold": 16, "diamond": 4},
    "gold":    {"cobblestone": 25, "iron": 25, "gold": 25, "diamond": 25},
    "diamond": {"cobblestone": 10, "iron": 10, "gold": 35, "diamond": 45},
}

SWORDS = {
    None:0,
    "wood":0,
    "stone":1,
    "iron":2,
    "gold":3,
    "diamond":5
    }
TIER_ORDER = ["wood", "stone", "iron", "gold", "diamond"]
# cumulative exp required for each level
LEVEL_EXP = {
    1:   7,    2:  16,   3:  27,   4:  40,   5:   55,
    6:  72,    7:  91,   8: 112,   9: 135,  10:  160,
    11: 187,   12: 216,  13: 247,  14: 280,  15:  315,
    16: 352,   17: 394,  18: 441,  19: 493,  20:  550,
    21: 612,   22: 679,  23: 751,  24: 828,  25:  910,
    26: 997,   27:1089,  28:1186, 29:1288, 30: 1395,
    31:1507,   32:1628,  33:1758, 34:1897, 35: 2045,
    36:2202,   37:2368,  38:2543, 39:2727, 40: 2920,
    41:3122,   42:3333,  43:3553, 44:3782, 45: 4020,
    46:4267,   47:4523,  48:4788, 49:5062, 50: 5345,
    51:5637,   52:5938,  53:6248, 54:6567, 55: 6895,
    56:7232,   57:7578,  58:7933, 59:8297, 60:8670
}

# which levels should get roles
MILESTONE_ROLES = [10,20,30,40,50]
SPAWN_CHANNEL_IDS = [1396534538498343002, 1396534603854123088,1396534658656763974,1396534732682035250]
REACT_CHANNELS = [1396534538498343002, 1396534603854123088,1396534658656763974,1396534732682035250,1396194783713824800]

ROLE_NAMES = {
    10:"Iron",
    20:"Gold",
    30:"Diamond",
    40:"Netherite"
}

CRAFT_RECIPES = {
    # tool        tier      wood   ore_count  ore_column    uses
    ("pickaxe",   "wood"):    (4,    0,      None, 10),
    ("pickaxe",   "stone"):   (1,    3,      "cobblestone", 10),
    ("pickaxe",   "iron"):    (1,    3,      "iron",        10),
    ("pickaxe",   "gold"):    (1,    3,      "gold",        10),
    ("pickaxe",   "diamond"): (1,    3,      "diamond",     10),

    ("hoe",       "wood"):    (4,    0,      None, 10),
    ("hoe",       "stone"):   (1,    2,      "cobblestone", 10),
    ("hoe",       "iron"):    (1,    2,      "iron",        10),
    ("hoe",       "gold"):    (1,    2,      "gold",        10),
    ("hoe",       "diamond"): (1,    2,      "diamond",     10),

    ("fishing_rod", "wood"):  (3,    0,      None,          10),
    ("fishing_rod", "stone"): (3,    0,      None,          10),
    ("fishing_rod", "iron"):  (3,    0,      None,          10),
    ("fishing_rod", "gold"):  (3,    0,      None,          10),
    ("fishing_rod", "diamond"):(3,   0,      None,          10),

    ("sword",     "stone"):    (1,    2,      "cobblestone",3),
    ("sword",     "iron"):    (1,    2,      "iron",        3),
    ("sword",     "gold"):    (1,    2,      "gold",        3),
    ("sword",     "diamond"): (1,    2,      "diamond",     3),

    ("axe",     "wood"):    (4,    0,      None, 5),
    ("axe",     "stone"):   (1,    3,      "cobblestone", 10),
    ("axe",     "iron"):    (1,    3,      "iron",        10),
    ("axe",     "gold"):    (1,    3,      "gold",        10),
    ("axe",     "diamond"): (1,    3,      "diamond",     10),
}
ITEMS = {"wood":{"useable":False,"category":"resource"},
        "gold":{"useable":False,"category":"resource"},
        "wheat":{"useable":False,"category":"resource"},
        "cobblestone":{"useable":False,"category":"resource"},
        "iron":{"useable":False,"category":"resource"},
        "gold":{"useable":False,"category":"resource"},
        "diamond":{"useable":False,"category":"resource"},
        "emerald":{"useable":False,"category":"emeralds"},
        "boss mob ticket":{"useable":True,"category":"items"}
        }
COLOR_MAP = {
    "white":  discord.Color.light_grey(),
    "green":  discord.Color.green(),
    "blue":   discord.Color.blue(),
    "purple": discord.Color.purple(),
    "red":    discord.Color.red(),
}
AXEWOOD = {None:1,"wood":2,"stone":3,"iron":4,"gold":5,"diamond":6}
# Spawn channels
SPAWN_CHANNEL_IDS = [1396534538498343002, 1396534603854123088,1396534658656763974,1396534732682035250]

MINECRAFT_COLORS = {
    "orange":    (255, 165, 0),
    "magenta":   (255, 0, 255),
    "light_blue": (102, 153, 216),
    "yellow":    (255, 255, 0),
    "lime":      (128, 255, 0),
    "pink":      (255, 192, 203),
    "gray":      (85, 85, 85),
    "cyan":      (0, 255, 255),
    "purple":    (128, 0, 128),
    "blue":      (0, 0, 255),
    "brown":     (139, 69, 19),
    "green":     (0, 128, 0),
    "red":       (255, 0, 0)
}
FISHTYPES = ["flopper","stripey","glitter","blockfish","betty","clayfish","kob","sunstreak","snooper","dasher","brinely","spotty"]

