import discord
from discord.ext import commands
import random, asyncio
from constants import *
from utils import *
DEATH_MESSAGES = [
    "💀 You ran into lava. You lost all your loot!",
    "☠️ You fell down a hole. You lost all your loot!",
    "👻 You didn't see the creeper around the corner. You lost all your loot!",
    "🕸️ The silverfish got you. You lost all your loot!",
    "🧟 You got lost and starved. You lost all your loot!"
]
STRONGHOLD_LOOT = {
                1:{"wood":{"min":1,"max":3},
                      "wheat":{"min":1,"max":3},
                      "cobblestone":{"min":1,"max":2},
                      "iron":{"min":1,"max":1}},

                5:{"wood":{"min":3,"max":10},
                      "wheat":{"min":3,"max":10},
                      "cobblestone":{"min":2,"max":4},
                      "iron":{"min":2,"max":4},
                      "gold":{"min":2,"max":4}},
    
                10:{"wood":{"min":10,"max":20},
                      "wheat":{"min":10,"max":20},
                      "cobblestone":{"min":10,"max":20},
                      "iron":{"min":8,"max":16},
                      "gold":{"min":5,"max":10},
                      "diamond":{"min":1,"max":1}},

                15:{"wood":{"min":20,"max":30},
                      "wheat":{"min":20,"max":30},
                      "cobblestone":{"min":10,"max":20},
                      "iron":{"min":6,"max":10},
                      "gold":{"min":8,"max":16},
                      "diamond":{"min":2,"max":10},
                      "emerald":{"min":2, "max":10}},

                20:{"wood":{"min":40,"max":100},
                      "wheat":{"min":40,"max":100},
                      "cobblestone":{"min":40,"max":100},
                      "iron":{"min":20,"max":80},
                      "gold":{"min":15,"max":50},
                      "diamond":{"min":15,"max":50},
                      "emerald":{"min":15, "max":100},
                      "boss mob ticket":{"min":1, "max":1}}     
                }


class PathButtons(discord.ui.View):
    def __init__(self, level, collected, player_id, db_pool):
        super().__init__()
        self.level = level
        self.collected = collected
        self.player_id = player_id
        self.db_pool = db_pool
        self.death_path = random.randint(1, 4)
        self.used_totem = False
        self.player_totems = 0
    async def async_init(self):
        async with self.db_pool.acquire() as conn:
            self.player_totems = await get_items(conn, self.player_id, "totem")

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.player_id

    async def handle_choice(self, interaction, path_chosen):
        try:
            if path_chosen == self.death_path:
                if self.used_totem or self.player_totems < 1:
                    self.disable_all_items()
                    await interaction.response.edit_message(
                        content=random.choice(DEATH_MESSAGES),
                        view=self
                    )
                    return
                else:
                    async with self.db_pool.acquire() as conn:
                        await take_items(self.player_id, "totem", 1, conn)
                    await interaction.response.edit_message(
                        content="Your totem saved you from death, be careful",
                        view=self
                    )
            # Survived — gain loot and go to next level
            self.death_path = random.randint(1, 4)
            next_level = self.level + 1
            current_tier = max([lvl for lvl in STRONGHOLD_LOOT.keys() if lvl <= next_level])
            loot_table = STRONGHOLD_LOOT[current_tier]

            # Pick a single reward
            item = random.choice(list(loot_table.keys()))
            bounds = loot_table[item]
            amount = random.randint(bounds["min"], bounds["max"])
            loot = {item: amount}

            # Update collected loot
            for item, amt in loot.items():
                self.collected[item] = self.collected.get(item, 0) + amt

            # Auto-leave if at level 25
            if next_level >= 25:
                self.disable_all_items()
                summary = "\n".join(f"{v}× {k}" for k, v in self.collected.items()) or "None"

                # 💾 Give collected items
                async with self.db_pool.acquire() as conn:
                    for item, amt in self.collected.items():
                        await give_items(self.player_id, item, amt, item, True, conn)

                await interaction.response.edit_message(
                    content=f"🎉 You've conquered all 25 levels of the stronghold!\n\n**Final Loot:**\n{summary}",
                    view=self
                )
                return

            # Else, move to next level
            embed = discord.Embed(title=f"Stronghold - Room {next_level}", color=discord.Color.dark_green())
            embed.add_field(name="🎁 Loot Found This Level", value="\n".join(f"{v}× {k}" for k, v in loot.items()), inline=False)
            embed.add_field(name="📦 Total Loot", value="\n".join(f"{v}× {k}" for k, v in self.collected.items()), inline=False)
            embed.set_footer(text="Choose a path...")

            next_view = PathButtons(next_level, self.collected, self.player_id, self.db_pool)
            await interaction.response.edit_message(embed=embed, view=next_view)

        except Exception as e:
            print(f"[Stronghold Error] {e}")
            await interaction.followup.send("❌ Something went wrong with the stronghold.", ephemeral=True)
    def disable_all_items(self):
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
    async def give_loot(self):
        async with self.db_pool.acquire() as conn:
            for item, amount in self.collected.items():
                await give_items(self.player_id, item, amount, ITEMS[item]["category"], ITEMS[item]["useable"], conn)

    @discord.ui.button(label="Path 1", style=discord.ButtonStyle.primary)
    async def path1(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_choice(interaction, 1)

    @discord.ui.button(label="Path 2", style=discord.ButtonStyle.primary)
    async def path2(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_choice(interaction, 2)

    @discord.ui.button(label="Path 3", style=discord.ButtonStyle.primary)
    async def path3(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_choice(interaction, 3)

    @discord.ui.button(label="Path 4", style=discord.ButtonStyle.primary)
    async def path4(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_choice(interaction, 4)

    @discord.ui.button(label="Leave", style=discord.ButtonStyle.danger)
    async def leave(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.disable_all_items()
        summary = "\n".join(f"{v}× {k}" for k, v in self.collected.items()) or "None"
        await interaction.response.edit_message(
            content=f"You left the stronghold safely!\n\n**Loot Collected:**\n{summary}",
            view=self
        )
        await self.give_loot()

# make sure give_items is implemented:
async def give_items(user_id, item_name, amount, category, useable, conn):
    await conn.execute("""
        INSERT INTO player_items (player_id, item_name, quantity, category, useable)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (player_id, item_name)
        DO UPDATE SET quantity = player_items.quantity + EXCLUDED.quantity
    """, user_id, item_name, amount, category, useable)
