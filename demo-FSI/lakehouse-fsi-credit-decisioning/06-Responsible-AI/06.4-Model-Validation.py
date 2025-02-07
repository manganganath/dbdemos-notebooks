# Databricks notebook source
# MAGIC %md-sandbox
# MAGIC
# MAGIC # Model Validation
# MAGIC
# MAGIC Machine learning (ML) models are increasingly being used in credit decisioning to automate lending processes, reduce costs, and improve accuracy. 
# MAGIC
# MAGIC As ML models become more complex and data-driven, their decision-making processes can become opaque, making it challenging to understand how decisions are made, and to ensure that they are fair and non-discriminatory. 
# MAGIC
# MAGIC Therefore, it is essential to develop techniques that enable model explainability and fairness in credit decisioning to ensure that the use of ML does not perpetuate existing biases or discrimination. 
# MAGIC
# MAGIC In this context, explainability refers to the ability to understand how an ML model is making its decisions, while fairness refers to ensuring that the model is not discriminating against certain groups of people. 
# MAGIC
# MAGIC ## Ensuring model fairness for new credit customers
# MAGIC
# MAGIC In this example, we'll make sure that our model behaves as expected and is fair for our new customers.
# MAGIC
# MAGIC We'll select our existing customers not having credit (We'll flag them as `defaulted = 2`) and make sure that our model is fair and behave the same among different group of the population.
# MAGIC
# MAGIC <!-- Collect usage data (view). Remove it to disable collection. View README for more details.  -->
# MAGIC <img width="1px" src="https://ppxrzfxige.execute-api.us-west-2.amazonaws.com/v1/analytics?category=lakehouse&org_id=1444828305810485&notebook=%2F03-Data-Science-ML%2F03.5-Explainability-and-Fairness-credit-decisioning&demo_name=lakehouse-fsi-credit&event=VIEW&path=%2F_dbdemos%2Flakehouse%2Flakehouse-fsi-credit%2F03-Data-Science-ML%2F03.5-Explainability-and-Fairness-credit-decisioning&version=1">

# COMMAND ----------

# MAGIC %pip install --quiet shap==0.46.0
# MAGIC dbutils.library.restartPython() 

# COMMAND ----------

# MAGIC %run ../_resources/00-setup $reset_all_data=false

# COMMAND ----------

# MAGIC %md
# MAGIC
# MAGIC Here we are merging several PII columns (hence we read from the ```customer_silver``` table) with the model prediction output table for visualizing them on the dashboard for end user consumption

# COMMAND ----------

feature_df = spark.table("credit_decisioning_features")
credit_bureau_label = spark.table("credit_bureau_gold")
customer_df = spark.table(f"customer_silver").select("cust_id", "gender", "first_name", "last_name", "email", "mobile_phone")
                   
df = (feature_df.join(customer_df, "cust_id", how="left")
               .join(credit_bureau_label, "cust_id", how="left")
               .withColumn("defaulted", F.when(col("CREDIT_DAY_OVERDUE").isNull(), 2)
                                         .when(col("CREDIT_DAY_OVERDUE") > 60, 1)
                                         .otherwise(0))
               .drop('CREDIT_DAY_OVERDUE')
               .fillna(0))
display(df)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Load Model from the registry

# COMMAND ----------

import mlflow

mlflow.set_registry_uri('databricks-uc')

model = mlflow.pyfunc.load_model(model_uri=f"models:/{catalog}.{db}.{model_name}@none")
features = model.metadata.get_input_schema().input_names()

# COMMAND ----------

underbanked_df = df[df.defaulted==2].toPandas() # Features for underbanked customers
banked_df = df[df.defaulted!=2].toPandas() # Features for rest of the customers

# COMMAND ----------

# MAGIC %md
# MAGIC ## Feature importance using Shapley values
# MAGIC
# MAGIC SHAP is a game-theoretic approach to explain machine learning models, providing a summary plot
# MAGIC of the relationship between features and model output. Features are ranked in descending order of
# MAGIC importance, and impact/color describe the correlation between the feature and the target variable.
# MAGIC - Generating SHAP feature importance is a very memory intensive operation.<br />
# MAGIC - To reduce the computational overhead of each trial, a single example is sampled from the underbanked set to explain.<br />
# MAGIC   For more thorough results, increase the sample size of explanations, or provide your own examples to explain.
# MAGIC - SHAP cannot explain models using data with nulls; if your dataset has any, both the background data and
# MAGIC   examples to explain will be imputed using the mode (most frequent values). This affects the computed
# MAGIC   SHAP values, as the imputed samples may not match the actual data distribution.
# MAGIC
# MAGIC For more information on how to read Shapley values, see the [SHAP documentation](https://shap.readthedocs.io/en/latest/example_notebooks/overviews/An%20introduction%20to%20explainable%20AI%20with%20Shapley%20values.html).

# COMMAND ----------

import shap

mlflow.autolog(disable=True)
mlflow.sklearn.autolog(disable=True)

train_sample = banked_df[features].sample(n=np.minimum(100, banked_df.shape[0]), random_state=42)
underbanked_sample = underbanked_df.sample(n=np.minimum(100, underbanked_df.shape[0]), random_state=42

# Use Kernel SHAP to explain feature importance on the sampled rows from the validation set.
predict = lambda x: model.predict(pd.DataFrame(x, columns=features).astype(train_sample.dtypes.to_dict()))

explainer = shap.KernelExplainer(predict, train_sample, link="identity")
shap_values = explainer.shap_values(underbanked_sample[features], l1_reg=False, nsamples=100)

# COMMAND ----------

# DBTITLE 1,Save feature importance
import matplotlib.pyplot as plt
import os

shap.summary_plot(shap_values, underbanked_sample[features], show=False)
plt.savefig(f"{os.getcwd()}/images/shap_feature_importance.png") 

# COMMAND ----------

# MAGIC %md
# MAGIC Shapely values can also help for the analysis of local, instance-wise effects. 
# MAGIC
# MAGIC We can also easily explain which feature impacted the decision for a given user. This can helps agent to understand the model an apply additional checks or control if required.

# COMMAND ----------

# DBTITLE 1,Explain feature importance for a single customer
#shap.initjs()
#We'll need to add shap bundle js to display nice graph
with open(shap.__file__[:shap.__file__.rfind('/')]+"/plots/resources/bundle.js", 'r') as file:
   shap_bundle_js = '<script type="text/javascript">'+file.read()+';</script>'

html = shap.force_plot(explainer.expected_value, shap_values[0,:], underbanked_sample[features].iloc[0,:])
displayHTML(shap_bundle_js + html.html())

# COMMAND ----------

# MAGIC %md
# MAGIC ## Model fairness using Shapley values
# MAGIC
# MAGIC In order to detect discriminatory outcomes in Machine Learning predictions, it is important to evaluate how the model treats various customer groups. This can be achieved by devising a metric, such as such as demographic parity, equal opportunity or equal odds, that defines fairness within the model. For example, when considering credit decisioning, we can compare the credit approval rates of male and female customers. In the notebook, we utilize Demographic Parity as a statistical measure of fairness, which asserts that there should be no difference between groups obtaining positive outcomes (e.g., credit approvals) in an ideal scenario. However, such perfect equality is rare, underscoring the need to monitor and address any gaps or discrepancies.

# COMMAND ----------

gender_array = underbanked_df['gender'].replace({'Female':0, 'Male':1}).to_numpy()[:100]
shap.group_difference_plot(shap_values.sum(1), \
                           gender_array, \
                           xmin=-1.0, xmax=1.0, \
                           xlabel="Demographic parity difference\nof model output for women vs. men")

# COMMAND ----------


shap_df = pd.DataFrame(shap_values, columns=features).add_suffix('_shap')
shap.group_difference_plot(shap_df[['age_shap', 'tenure_months_shap']].to_numpy(), \
                           gender_array, \
                           feature_names=['age', 'tenure_months'], 
                           xmin=-0.5, xmax=0.5, \
                           xlabel="Demographic parity difference\nof SHAP values for women vs. men")                        

# COMMAND ----------

# MAGIC %md
# MAGIC ## Logging custom metrics with **MLflow**

# COMMAND ----------

# Retrieve model version by alias
client = mlflow.tracking.MlflowClient()
model_version_info = client.get_model_version_by_alias(name=f"{catalog}.{db}.{model_name}", alias="none")

# Log new artifacts in the same experiment
with mlflow.start_run(run_id=model_version_info.run_id):
    # Log SHAP feature importance
    mlflow.log_artifact(f"{os.getcwd()}/images/shap_feature_importance.png")

    #Log Demographic parity difference\nof model output for women vs. men
    mean_shap_male = np.mean(shap_values[gender_array == 1])
    mean_shap_female = np.mean(shap_values[gender_array == 0])
    mean_difference = mean_shap_male - mean_shap_female
    mlflow.log_metric("shap_demo_parity_diff_wm", mean_shap_male - mean_shap_female)

# COMMAND ----------

# MAGIC %md
# MAGIC
# MAGIC ## Store Data (into Delta format) for Downstream Usage
# MAGIC
# MAGIC Since we want to add the Explainability and Fairness assessment in the business dashboards, we will persist this data into Delta format and query it later.

# COMMAND ----------

#Let's load the underlying model to get the proba
skmodel = mlflow.sklearn.load_model(model_uri=f"models:/{catalog}.{db}.{model_name}@none")
underbanked_sample['default_prob'] = skmodel.predict_proba(underbanked_sample[features])[:,1]
underbanked_sample['prediction'] = skmodel.predict(underbanked_sample[features])
final_df = pd.concat([underbanked_sample.reset_index(), shap_df], axis=1)

final_df = spark.createDataFrame(final_df).withColumn("default_prob", col("default_prob").cast('double'))
display(final_df)
final_df.drop('CREDIT_CURRENCY', '_rescued_data', 'index') \
        .write.mode("overwrite").option('OverwriteSchema', True).saveAsTable(f"shap_explanation")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Compliance checks
# MAGIC
# MAGIC Our model is now ready and registed in Unity Catalog with 'None' alias. 
# MAGIC
# MAGIC Let's assume that the absolute demographic parity difference of model output for women vs. men should be less than 0.1 to model to pass the compliance checks.
# MAGIC

# COMMAND ----------

import mlflow

# Retrieve experiment run by alias
client = mlflow.tracking.MlflowClient()
model_info = client.get_model_version_by_alias(name=f"{catalog}.{db}.{model_name}", alias="None")
run = client.get_run(model_info.run_id)

# Retrieve a specific metric, such as 'shap_demo_parity_diff_wm'
shap_demo_parity_diff_wm = run.data.metrics.get("shap_demo_parity_diff_wm")

# COMMAND ----------

# Check whether the metric passes the requirements

compliance_checks_passed = False

if abs(shap_demo_parity_diff_wm) < 0.1:
  compliance_checks_passed = True
  print("compliance checks passed")
else:
  print("compliance checks faield")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Change the model stage to Staging
# MAGIC
# MAGIC Our model is now ready. We can review the notebook generated by the auto-ml run and customize if if required.
# MAGIC
# MAGIC For this demo, we'll consider that our model has passed compliance checks and pre-development tests. It's now ready to change the stage to Staging.

# COMMAND ----------

if compliance_checks_passed:
  # Set model version tag
  client.set_model_version_tag(f"{catalog}.{db}.{model_name}", model_info.version, "compliance_checks", "passed")

  # Flag it as Staging using UC Aliases
  client.delete_registered_model_alias(name=f"{catalog}.{db}.{model_name}", alias="None")
  client.set_registered_model_alias(name=f"{catalog}.{db}.{model_name}", alias="Staging", version=model_info.version)

  print(f'Version {model_info.version} of {catalog}.{db}.{model_name} is now the staging version.')

# COMMAND ----------


