import sys, types
sm=types.ModuleType('subscription_manager'); sm._get_db=lambda:None; sys.modules['subscription_manager']=sm
disc=types.ModuleType('discord'); ui=types.ModuleType('discord.ui')
class _V:
    def __init__(self,*a,**k): pass
    def add_item(self,*a,**k): pass
ui.View=_V; ui.Select=_V; ui.Button=_V
disc.ui=ui; disc.SelectOption=lambda *a,**k: None
class _BS: success=primary=secondary=danger=1
disc.ButtonStyle=_BS; disc.File=lambda *a,**k: None
sys.modules['discord']=disc; sys.modules['discord.ui']=ui
from career import career_manager as CM, career_ui
c=CM.new_career(123,"Jai Patel","legspin","aggressor")
c["attributes"]={"power":74,"control":80,"bowling":71,"stamina":76}; CM.refresh_ovr(c)
c["coins"]=4820; c["debut_done"]=True; c["cosmetic_title"]="[Patron]"
print("OVR",c["ovr"],"tier",c["tier"])
open("career_card.png","wb").write(career_ui.render_career_card(c).read())
print("wrote career_card.png")
