import json
KEYS=json.load(open("deleg_keys.json"))
LAB="S S S H S S S H X S H H H H S H H H S S S S S S X H S H S X X S S S S S S S S S H S H X H S H S H S H".split()
GOLD=dict(zip(KEYS,LAB))
_NEW=json.load(open("tolabel.json"))
_L2={0:"H",3:"X",5:"H",6:"H",7:"S",9:"S",10:"X",11:"X",12:"S",16:"X",17:"X",20:"S",21:"X",22:"S",23:"S",24:"S",25:"S",26:"S",27:"H",29:"X",30:"S"}
for i,l in _L2.items(): GOLD[_NEW[i]]=l
json.dump(GOLD,open("gold.json","w"))
