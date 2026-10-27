import { readFile } from "node:fs/promises";
import Ajv2020 from "ajv/dist/2020.js";
import addFormats from "ajv-formats";

const ajv = new Ajv2020({ allErrors: true, strict: true });
addFormats(ajv);

export const compileSchema = async (file) => {
  const schema = JSON.parse(await readFile(file, "utf8"));
  return ajv.compile(schema);
};

export const assertValid = (validator, value, label) => {
  if (validator(value)) return;
  const details = validator.errors
    .map((error) => `${error.instancePath || "/"} ${error.message}`)
    .join("; ");
  throw new Error(`${label} failed schema validation: ${details}`);
};

