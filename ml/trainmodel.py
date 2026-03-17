import pandas as pd
import pickle
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import LabelEncoder
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score

df = pd.read_excel("ml/dataset.xlsx")

features = [
'Mean_ACC_ANS',
'Mean_RTs_ANS',
'wm_K',
'Accuracy_SymbolicComp',
'RTs_SymbolicComp',
'Accuracy_Fraction',
'RTs_Fraction'
]

X = df[features]
y = df["Risk_Level"]

encoder = LabelEncoder()
y = encoder.fit_transform(y)

X_train,X_test,y_train,y_test = train_test_split(
X,y,test_size=0.2,random_state=42
)

model = RandomForestClassifier(n_estimators=200)

model.fit(X_train,y_train)

pred = model.predict(X_test)

print("Model Accuracy:",accuracy_score(y_test,pred))

pickle.dump(model,open("models/model.pkl","wb"))
pickle.dump(encoder,open("models/label_encoder.pkl","wb"))